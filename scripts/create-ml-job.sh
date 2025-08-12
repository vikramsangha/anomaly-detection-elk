#!/bin/sh

ES_HOST="http://elasticsearch:9200"
KIBANA_HOST="http://kibana:5601"
INDEX_NAME="test-logs"
JOB_ID="analyze_test_results"
DATAFEED_ID="datafeed-$JOB_ID"

set -e
# Wait for Kibana
echo "⌛ Waiting for Kibana..."
until curl -s -f "$KIBANA_HOST/api/status" >/dev/null; do
  sleep 5
done

# Create Data View
echo "🔄 Creating data view..."
RESPONSE=$(curl -s -X POST "$KIBANA_HOST/api/saved_objects/index-pattern" \
  -H "kbn-xsrf: true" \
  -H "Content-Type: application/json" \
  -d '{
    "attributes": {
      "title": "test-logs",
      "timeFieldName": "timestamp"
    }
  }')

# Extract ID using shell tools
DATA_VIEW_ID=$(echo "$RESPONSE" | grep -o '"id":"[^"]*' | cut -d'"' -f4)

if [ -z "$DATA_VIEW_ID" ]; then
  echo "❌ Failed to create data view"
  echo "$RESPONSE"
  exit 1
fi


# Set time range
# Set default time range in Kibana to include future data
curl -s -X POST "$KIBANA_HOST/api/kibana/settings" \
  -H 'kbn-xsrf: true' \
  -H 'Content-Type: application/json' \
  -d '{
    "changes": {
      "timepicker:timeDefaults": "{\"from\":\"now-50d\",\"to\":\"now\"}"
    }
  }'

echo "✅ Kibana time range updated"

sleep 10


# --- Get earliest & latest timestamps from data ---
EARLIEST=$(curl -s -X POST "$ES_HOST/$INDEX_NAME/_search" \
  -H 'Content-Type: application/json' \
  -d '{"size":1,"sort":[{"timestamp":"asc"}],"_source":["timestamp"]}' \
  | grep -o '"timestamp":[0-9]*' | head -1 | grep -o '[0-9]*')

LATEST=$(curl -s -X POST "$ES_HOST/$INDEX_NAME/_search" \
  -H 'Content-Type: application/json' \
  -d '{"size":1,"sort":[{"timestamp":"desc"}],"_source":["timestamp"]}' \
  | grep -o '"timestamp":[0-9]*' | head -1 | grep -o '[0-9]*')

if [ -z "$EARLIEST" ] || [ -z "$LATEST" ]; then
  echo "❌ Could not extract timestamps from Elasticsearch. Check if 'timestamp' is mapped as a date."
  exit 1
fi

echo "Earliest: $EARLIEST"
echo "Latest:   $LATEST"

# Convert epoch millis to ISO 8601
START_TIME=$(date -u -d @"$(($EARLIEST/1000))" +"%Y-%m-%dT%H:%M:%SZ")
END_TIME=$(date -u -d @"$(($LATEST/1000))" +"%Y-%m-%dT%H:%M:%SZ")

echo "Using range: $START_TIME → $END_TIME"

echo "----------------------Creating ML Job Now-------------------------->"
curl -s -X PUT "$ES_HOST/_ml/anomaly_detectors/$JOB_ID" \
  -H 'Content-Type: application/json' \
  -d "{
    \"description\": \"Multi-metric anomaly detection for test results\",
    \"analysis_config\": {
      \"bucket_span\": \"15m\",
      \"detectors\": [
        {
          \"function\": \"mean\",
          \"field_name\": \"time\",
          \"partition_field_name\": \"test_name\",
          \"detector_description\": \"mean(time) by test_name\"
        }
      ],
      \"influencers\": [\"test_name\"]
    },
    \"data_description\": {
      \"time_field\": \"timestamp\",
      \"time_format\": \"epoch_ms\"
    },
    \"custom_settings\": {
      \"created_by\": \"multi-metric-wizard\"
    }
  }"

echo "----------------------Creating DataFeed Now-------------------------->"
# --- Create datafeed ---
curl -s -X PUT "$ES_HOST/_ml/datafeeds/$DATAFEED_ID" \
  -H 'Content-Type: application/json' \
  -d "{
    \"job_id\": \"$JOB_ID\",
    \"indices\": [\"$INDEX_NAME\"],
    \"query\": { \"match_all\": {} }
  }"

echo "----------------------Open Job-------------------------->"
curl -s -X POST "$ES_HOST/_ml/anomaly_detectors/$JOB_ID/_open" \
  -H 'Content-Type: application/json'

echo "----------------------Start DataFeed Now-------------------------->"
curl -s -X POST "$ES_HOST/_ml/datafeeds/$DATAFEED_ID/_start" \
  -H 'Content-Type: application/json' \
  -d "{
    \"start\": \"$START_TIME\",
    \"end\": \"$END_TIME\"
  }"

echo "----------------------Sync Jobs via Kibana API-------------------------->"
# Dry run first
curl -s -X GET "$KIBANA_HOST/api/ml/saved_objects/sync?simulate=true" \
  -H 'kbn-xsrf: true' \
  -H 'Content-Type: application/json'

# Actual sync
curl -s -X GET "$KIBANA_HOST/api/ml/saved_objects/sync?simulate=false" \
  -H 'kbn-xsrf: true' \
  -H 'Content-Type: application/json'


echo "✅ ML job created with Mean(time), split by test_name, influencers=test_name, started immediately, and synced to Kibana."



#!/bin/bash

# Wait for Elasticsearch to become available
until curl -s -XGET --insecure http://elasticsearch:9200; do
  sleep 5
done

# Check ES is reachable
curl -s http://elasticsearch:9200

# Correct call — no body!
curl -XPOST "http://elasticsearch:9200/_license/start_trial?acknowledge=true"
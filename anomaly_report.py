import requests
import json
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from datetime import datetime, timedelta
from fpdf import FPDF
import os

# Configuration
ES_HOST = "http://localhost:9200"  # Connect to local Docker
JOB_ID = "analyze_test_results"    # Your ML job ID
REPORTS_DIR = "reports"            # Reports directory name
REPORT_FILE = os.path.join(REPORTS_DIR, "anomaly_report.pdf")
IMAGE_FILE = os.path.join(REPORTS_DIR, "anomaly_chart.png")
DAYS_BACK = 50  # Look back 50 days to match your data

def ensure_reports_directory():
    """Create reports directory if it doesn't exist"""
    if not os.path.exists(REPORTS_DIR):
        os.makedirs(REPORTS_DIR)
        print(f"Created reports directory: {os.path.abspath(REPORTS_DIR)}")
    else:
        print(f"Using existing reports directory: {os.path.abspath(REPORTS_DIR)}")

def test_connection():
    """Test connection to Elasticsearch"""
    try:
        response = requests.get(f"{ES_HOST}/_cluster/health")
        print(f"Elasticsearch cluster status: {response.json()['status']}")
        return True
    except Exception as e:
        print(f"Cannot connect to Elasticsearch: {e}")
        return False

def check_ml_job():
    """Check if ML job exists and is running"""
    try:
        response = requests.get(f"{ES_HOST}/_ml/anomaly_detectors/{JOB_ID}")
        if response.status_code == 200:
            job_info = response.json()
            print(f"ML Job found: {job_info['jobs'][0]['job_id']}")
            
            # Get job stats
            stats_response = requests.get(f"{ES_HOST}/_ml/anomaly_detectors/{JOB_ID}/_stats")
            if stats_response.status_code == 200:
                stats = stats_response.json()['jobs'][0]
                print(f"Job state: {stats['state']}")
                print(f"Processed records: {stats.get('data_counts', {}).get('processed_record_count', 0)}")
            return True
        else:
            print(f"ML Job {JOB_ID} not found")
            return False
    except Exception as e:
        print(f"Error checking ML job: {e}")
        return False

def fetch_anomaly_data():
    """Fetch anomaly records from Elasticsearch ML API"""
    
    # Calculate time range
    end_time = datetime.now()
    start_time = end_time - timedelta(days=DAYS_BACK)
    
    # Use the correct endpoint for anomaly records
    url = f"{ES_HOST}/.ml-anomalies-{JOB_ID}/_search"
    
    payload = {
        "size": 1000,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"job_id": JOB_ID}},
                    {"term": {"result_type": "record"}},
                    {"range": {
                        "timestamp": {
                            "gte": int(start_time.timestamp() * 1000),
                            "lte": int(end_time.timestamp() * 1000)
                        }
                    }},
                    {"range": {
                        "record_score": {
                            "gte": 0  # Get all anomalies, even low scores
                        }
                    }}
                ]
            }
        },
        "sort": [
            {"record_score": {"order": "desc"}}
        ]
    }
    
    headers = {'Content-Type': 'application/json'}
    
    try:
        print(f"Querying: {url}")
        print(f"Time range: {start_time} to {end_time}")
        
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 404:
            # Try alternative index pattern
            url = f"{ES_HOST}/.ml-anomalies-shared/_search"
            response = requests.post(url, json=payload, headers=headers)
        
        response.raise_for_status()
        data = response.json()
        
        if 'hits' not in data or not data['hits']['hits']:
            print("No anomaly records found.")
            print("Debug info:")
            print(f"Total hits: {data.get('hits', {}).get('total', {}).get('value', 0)}")
            return None
            
        records = [hit['_source'] for hit in data['hits']['hits']]
        print(f"Fetched {len(records)} anomaly records.")
        
        # Filter for significant anomalies
        significant_records = [r for r in records if r.get('record_score', 0) > 0]
        print(f"Found {len(significant_records)} significant anomalies (score > 0)")
        
        return significant_records
        
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error fetching data: {e}")
        print(f"Response: {e.response.text if e.response else 'No response'}")
        
        # Try to list available ML indices
        try:
            indices_response = requests.get(f"{ES_HOST}/_cat/indices/.ml-*?v")
            print("Available ML indices:")
            print(indices_response.text)
        except:
            pass
            
        return None
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

def fetch_bucket_data():
    """Fetch bucket results as alternative data source"""
    url = f"{ES_HOST}/.ml-anomalies-{JOB_ID}/_search"
    
    payload = {
        "size": 1000,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"job_id": JOB_ID}},
                    {"term": {"result_type": "bucket"}}
                ]
            }
        },
        "sort": [{"timestamp": {"order": "asc"}}]
    }
    
    try:
        response = requests.post(url, json=payload, headers={'Content-Type': 'application/json'})
        if response.status_code == 404:
            url = f"{ES_HOST}/.ml-anomalies-shared/_search"
            response = requests.post(url, json=payload, headers={'Content-Type': 'application/json'})
        
        if response.status_code == 200:
            data = response.json()
            if data['hits']['hits']:
                buckets = [hit['_source'] for hit in data['hits']['hits']]
                print(f"Fetched {len(buckets)} bucket results as fallback")
                return buckets
    except Exception as e:
        print(f"Could not fetch bucket data: {e}")
    
    return None

def generate_visualization(records):
    """Create matplotlib visualization of anomalies"""
    if not records:
        return False
    
    # Prepare data
    timestamps = []
    scores = []
    test_names = []
    actual_values = []
    typical_values = []
    
    for record in records:
        # Handle timestamp
        if 'timestamp' in record:
            ts = record['timestamp']
            if isinstance(ts, (int, float)):
                timestamps.append(datetime.fromtimestamp(ts/1000))
            else:
                timestamps.append(datetime.fromisoformat(ts.replace('Z', '+00:00')))
        
        # Get anomaly score
        scores.append(record.get('record_score', 0))
        
        # Get test name from partition field
        if 'partition_field_value' in record:
            test_names.append(record['partition_field_value'])
        else:
            test_names.append("Unknown")
        
        # Get actual and typical values if available
        actual_values.append(record.get('actual', [0])[0] if 'actual' in record else 0)
        typical_values.append(record.get('typical', [0])[0] if 'typical' in record else 0)
    
    if not timestamps:
        print("No timestamp data for visualization.")
        return False
    
    # Create figure with subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
    
    # Top plot: Anomaly scores over time
    unique_tests = list(set(test_names))
    colors = plt.cm.tab10(np.linspace(0, 1, len(unique_tests)))
    color_map = {test: colors[i] for i, test in enumerate(unique_tests)}
    
    for ts, score, test in zip(timestamps, scores, test_names):
        ax1.scatter(ts, score, color=color_map[test], s=100, alpha=0.7)
    
    ax1.set_title(f"Anomaly Detection Report: {JOB_ID}", fontsize=16, fontweight='bold')
    ax1.set_ylabel("Anomaly Score", fontsize=12)
    ax1.set_xlabel("Timestamp", fontsize=12)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax1.grid(True, alpha=0.3)
    
    # Add horizontal lines for severity levels
    ax1.axhline(y=75, color='r', linestyle='--', alpha=0.5, label='Critical (>75)')
    ax1.axhline(y=50, color='orange', linestyle='--', alpha=0.5, label='Major (>50)')
    ax1.axhline(y=25, color='yellow', linestyle='--', alpha=0.5, label='Minor (>25)')
    
    # Create legend for test names
    legend_handles = []
    for test, color in color_map.items():
        legend_handles.append(plt.Line2D([0], [0], marker='o', color='w', 
                            markerfacecolor=color, markersize=10, label=test))
    ax1.legend(handles=legend_handles, title="Test Names", loc='upper right')
    
    # Bottom plot: Actual vs Typical values (if available)
    if any(actual_values) or any(typical_values):
        ax2.plot(timestamps, actual_values, 'b-', label='Actual', alpha=0.7)
        ax2.plot(timestamps, typical_values, 'g--', label='Typical', alpha=0.7)
        ax2.fill_between(timestamps, actual_values, typical_values, alpha=0.3, color='red', where=[a > t for a, t in zip(actual_values, typical_values)])
        ax2.set_ylabel("Test Execution Time (ms)", fontsize=12)
        ax2.set_xlabel("Timestamp", fontsize=12)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax2.grid(True, alpha=0.3)
        ax2.legend()
        ax2.set_title("Actual vs Expected Performance", fontsize=14)
    
    # Rotate x-axis labels
    fig.autofmt_xdate()
    
    # Save image
    plt.tight_layout()
    plt.savefig(IMAGE_FILE, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Visualization saved to {os.path.abspath(IMAGE_FILE)}")
    return True

def create_pdf_report(records):
    """Generate PDF report with visualization and summary"""
    pdf = FPDF()
    pdf.add_page()
    
    # Title
    pdf.set_font("Arial", size=20, style='B')
    pdf.cell(0, 15, f"ML Anomaly Detection Report", ln=1, align='C')
    pdf.set_font("Arial", size=14, style='B')
    pdf.cell(0, 10, f"Job ID: {JOB_ID}", ln=1, align='C')
    pdf.set_font("Arial", size=10)
    pdf.cell(0, 8, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ln=1, align='C')
    pdf.ln(5)
    
    # Add statistics
    if records:
        pdf.set_font("Arial", size=12, style='B')
        pdf.cell(0, 10, "Summary Statistics:", ln=1)
        pdf.set_font("Arial", size=10)
        
        total_anomalies = len(records)
        critical = len([r for r in records if r.get('record_score', 0) > 75])
        major = len([r for r in records if 50 < r.get('record_score', 0) <= 75])
        minor = len([r for r in records if 25 < r.get('record_score', 0) <= 50])
        
        pdf.cell(0, 6, f"Total Anomalies Detected: {total_anomalies}", ln=1)
        pdf.cell(0, 6, f"  - Critical (score > 75): {critical}", ln=1)
        pdf.cell(0, 6, f"  - Major (50 < score <= 75): {major}", ln=1)
        pdf.cell(0, 6, f"  - Minor (25 < score <= 50): {minor}", ln=1)
        pdf.ln(5)
    
    # Add image if exists
    if os.path.exists(IMAGE_FILE):
        # Calculate position to center the image
        pdf.image(IMAGE_FILE, x=10, y=pdf.get_y(), w=190)
        pdf.ln(120)  # Move cursor below image
    
    # Add findings section
    pdf.add_page()
    pdf.set_font("Arial", size=14, style='B')
    pdf.cell(0, 10, "Key Findings:", ln=1)
    pdf.set_font("Arial", size=10)
    
    if records:
        # Get top anomalies
        top_anomalies = sorted(records, key=lambda x: x.get('record_score', 0), reverse=True)[:5]
        
        pdf.cell(0, 8, "Top 5 Anomalies:", ln=1)
        for i, anomaly in enumerate(top_anomalies, 1):
            test_name = anomaly.get('partition_field_value', 'Unknown')
            score = anomaly.get('record_score', 0)
            ts = anomaly.get('timestamp', 0)
            if isinstance(ts, (int, float)):
                ts_str = datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M')
            else:
                ts_str = ts
            
            pdf.cell(0, 6, f"  {i}. Test: {test_name}, Score: {score:.1f}, Time: {ts_str}", ln=1)
    
    pdf.ln(5)
    pdf.set_font("Arial", size=10)
    pdf.multi_cell(0, 6, 
        "\nRecommendations:\n"
        "1. Investigate tests with critical anomaly scores (>75)\n"
        "2. Review performance during peak anomaly periods\n"
        "3. Consider infrastructure scaling if anomalies correlate with load\n"
        "4. Implement alerting for real-time anomaly detection\n"
        "5. Regular review of ML job configuration for optimization")
    
    # Save PDF
    pdf.output(REPORT_FILE)
    print(f"PDF report generated: {os.path.abspath(REPORT_FILE)}")

def main():
    print("="*50)
    print("ML ANOMALY REPORT GENERATOR")
    print("="*50)
    
    # Ensure reports directory exists
    ensure_reports_directory()
    
    # Test connection
    if not test_connection():
        print("Cannot connect to Elasticsearch. Make sure Docker is running.")
        return
    
    # Check ML job
    if not check_ml_job():
        print("ML job not found or not running.")
        return
    
    # Fetch data
    print(f"\nFetching anomaly data (last {DAYS_BACK} days)...")
    records = fetch_anomaly_data()
    
    if not records:
        print("\nTrying to fetch bucket data as fallback...")
        records = fetch_bucket_data()
    
    if not records:
        print("\nNo data available for report generation.")
        print("Make sure the ML job has processed data and generated anomalies.")
        return
    
    # Generate visualization
    print(f"\nCreating visualization from {len(records)} records...")
    viz_success = generate_visualization(records)
    
    # Create PDF report
    print("\nGenerating PDF report...")
    create_pdf_report(records)
    
    # Cleanup
    if viz_success and os.path.exists(IMAGE_FILE):
        print(f"Keeping {IMAGE_FILE} for reference")
    
    print("\n" + "="*50)
    print("REPORT GENERATION COMPLETED")
    print(f"Files generated:")
    print(f"  - {os.path.abspath(REPORT_FILE)}")
    print(f"  - {os.path.abspath(IMAGE_FILE)}")
    print("="*50)

if __name__ == "__main__":
    main()
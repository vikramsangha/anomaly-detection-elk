import requests
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ===== CONFIG =====
ES_HOST = "http://localhost:9200"
KIBANA_HOST = "http://localhost:5601"
JOB_ID = "analyze_test_results"
DAYS_BACK = 50
REPORTS_DIR = "reports"
MD_FILE = os.path.join(REPORTS_DIR, "anomaly_summary.md")
TREND_IMG = os.path.join(REPORTS_DIR, "anomaly_trend.png")
TOP_TESTS_IMG = os.path.join(REPORTS_DIR, "top_tests.png")

# ML Explorer link
ML_EXPLORER_URL = f"{KIBANA_HOST}/app/ml/explorer?_g=(ml:(jobIds:!('{JOB_ID}')))"

# ===== HELPERS =====
def ensure_reports_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)

def fetch_anomalies():
    end_time = datetime.now()
    start_time = end_time - timedelta(days=DAYS_BACK)
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
                    {"range": {"record_score": {"gte": 50}}}
                ]
            }
        },
        "sort": [{"timestamp": {"order": "asc"}}]
    }
    headers = {"Content-Type": "application/json"}
    r = requests.post(url, json=payload, headers=headers)
    if r.status_code == 404:
        url = f"{ES_HOST}/.ml-anomalies-shared/_search"
        r = requests.post(url, json=payload, headers=headers)
    r.raise_for_status()
    return [hit["_source"] for hit in r.json()["hits"]["hits"]]

def group_anomalies(records):
    grouped = defaultdict(list)
    for rec in records:
        test_name = rec.get("partition_field_value", "Unknown")
        ts = rec.get("timestamp")
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts / 1000)
        else:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        grouped[test_name].append({
            "timestamp": ts,
            "score": rec.get("record_score", 0)
        })
    return grouped

def save_markdown(grouped):
    with open(MD_FILE, "w") as f:
        f.write(f"# Anomaly Summary (Score > 50)\n\n")
        f.write(f"ML Explorer: [{ML_EXPLORER_URL}]({ML_EXPLORER_URL})\n\n")
        f.write("| Test Name | Date | Score |\n")
        f.write("|-----------|------|-------|\n")
        for test, anomalies in grouped.items():
            for a in anomalies:
                f.write(f"| {test} | {a['timestamp'].strftime('%Y-%m-%d')} | {a['score']:.1f} |\n")

def plot_trend(grouped):
    plt.figure(figsize=(12, 6))
    for test, anomalies in grouped.items():
        dates = [a["timestamp"] for a in anomalies]
        scores = [a["score"] for a in anomalies]
        plt.plot(dates, scores, marker="o", label=test)
    plt.axhline(y=75, color='r', linestyle='--', alpha=0.5, label='Critical')
    plt.axhline(y=50, color='orange', linestyle='--', alpha=0.5, label='Major')
    plt.title("Anomaly Trend (Score > 50)")
    plt.xlabel("Date")
    plt.ylabel("Score")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(TREND_IMG, dpi=150)
    plt.close()

def plot_top_tests(grouped):
    top_counts = sorted([(test, len(anoms)) for test, anoms in grouped.items()],
                        key=lambda x: x[1], reverse=True)[:10]
    tests = [t[0] for t in top_counts]
    counts = [t[1] for t in top_counts]
    plt.figure(figsize=(10, 6))
    plt.barh(tests, counts, color="skyblue")
    plt.xlabel("Number of Anomalies")
    plt.ylabel("Test Name")
    plt.title("Top Tests by Anomaly Count (Score > 50)")
    plt.gca().invert_yaxis()
    plt.tight_layout()
    plt.savefig(TOP_TESTS_IMG, dpi=150)
    plt.close()

# ===== MAIN =====
if __name__ == "__main__":
    ensure_reports_dir()
    print("Fetching anomalies...")
    anomalies = fetch_anomalies()
    if not anomalies:
        print("No anomalies found above score 50.")
        exit(0)

    grouped = group_anomalies(anomalies)

    print("Saving markdown summary...")
    save_markdown(grouped)

    print("Plotting trend graph...")
    plot_trend(grouped)

    print("Plotting top tests chart...")
    plot_top_tests(grouped)

    print("\n=== ML Explorer Link ===")
    print(ML_EXPLORER_URL)
    print("========================\n")
    print(f"Markdown: {os.path.abspath(MD_FILE)}")
    print(f"Trend Image: {os.path.abspath(TREND_IMG)}")
    print(f"Top Tests Image: {os.path.abspath(TOP_TESTS_IMG)}")

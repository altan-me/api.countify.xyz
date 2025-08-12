import os
import sys
import uuid
import tempfile
import importlib

# Ensure project root is on sys.path so we can import app.py when run as a script
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def generate_id() -> str:
    return f"cli-{uuid.uuid4().hex[:12]}"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def main() -> None:
    # Use a persistent temp dir to avoid Windows file-lock issues
    temp_dir = tempfile.mkdtemp(prefix="counter_api_")
    os.environ["DATABASE"] = os.path.join(temp_dir, "counters.test.db")

    try:
        app_module = importlib.import_module("app")
        flask_app = app_module.create_app()
        client = flask_app.test_client()

        # 1) Dashboard
        r = client.get("/")
        if r.status_code != 200:
            fail(f"Dashboard status {r.status_code}")

        # 2) get-total initializes to 0
        counter_a = generate_id()
        r = client.get(f"/get-total/{counter_a}")
        if r.status_code != 200 or r.get_json().get("count") != 0:
            fail("get-total did not initialize to 0")

        # 3) increment twice -> 2
        r = client.post(f"/increment/{counter_a}")
        if r.status_code != 200 or r.get_json().get("count") != 1:
            fail("increment did not reach 1")
        r = client.post(f"/increment/{counter_a}")
        if r.status_code != 200 or r.get_json().get("count") != 2:
            fail("increment did not reach 2")

        # 4) increase path
        counter_b = generate_id()
        r = client.post(f"/increase/{counter_b}", json={"value": 5})
        if r.status_code != 200 or r.get_json().get("count") != 5:
            fail("increase did not set to 5")
        r = client.post(f"/increase/{counter_b}", json={"value": 50})
        if r.status_code != 200 or r.get_json().get("count") != 55:
            fail("increase did not total 55")

        # 5) stats shape
        r = client.get("/stats")
        if r.status_code != 200:
            fail("stats did not return 200")
        stats = r.get_json()
        for key in ["total_counters", "total_count", "last_activity", "top_counters", "recently_updated"]:
            if key not in stats:
                fail(f"stats missing key: {key}")

        print("OK: All smoke tests passed")
    except Exception as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()



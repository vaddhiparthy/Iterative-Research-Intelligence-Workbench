import time
import traceback

try:
    from app.app import app, start_runtime

    start_runtime()
    print("Imported app.app:app")
    print("Routes:", [r.rule for r in app.url_map.iter_rules()])
    app.run(host="0.0.0.0", port=9990, threaded=True)
except Exception:
    print("DeepResearcher failed to start:")
    traceback.print_exc()
    print("Holding container alive for inspection...")
    while True:
        time.sleep(3600)

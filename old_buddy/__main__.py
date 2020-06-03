from .old_buddy import OldBuddy

if __name__ == "__main__":
    drat = OldBuddy()
    try:
        drat.telemetry_trhead.join()
    except KeyboardInterrupt:
        drat.stop()
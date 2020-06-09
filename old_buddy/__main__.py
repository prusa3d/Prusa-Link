from .old_buddy import OldBuddy

if __name__ == "__main__":
    old_buddy = OldBuddy()
    try:
        old_buddy.telemetry_thread.join()
    except KeyboardInterrupt:
        old_buddy.stop()

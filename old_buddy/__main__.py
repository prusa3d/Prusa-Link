from .old_buddy import OldBuddy


def main():
    old_buddy = OldBuddy()
    try:
        old_buddy.stopped_event.wait()
    except KeyboardInterrupt:
        old_buddy.stop()


if __name__ == '__main__':
    main()

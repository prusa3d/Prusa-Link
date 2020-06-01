from .prusa_drat import PrusaConnectMK3

if __name__ == "__main__":
    drat = PrusaConnectMK3()
    try:
        drat.telemetry_trhead.join()
    except KeyboardInterrupt:
        drat.stop()
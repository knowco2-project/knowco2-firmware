"""One-shot Reverse TFT benchmark. Run as a code file (see README), not a REPL import."""


def run():
    import board
    import gc
    import json
    import time

    if board.board_id != "adafruit_feather_esp32s3_reverse_tft":
        raise RuntimeError("Use the Feather ESP32-S3 Reverse TFT")

    from knowco2 import config, state, ui, version

    display = board.DISPLAY
    auto_refresh = display.auto_refresh
    gc.collect()
    print("KnowCO2 bench:", version.CP_VERSION, board.board_id,
          "UI heap free (bytes):", gc.mem_free())
    # Real KnowCO2 widget tree and drawing functions, deterministic synthetic
    # readings. No sensor, network, settings or flash writes in this program.
    state.screen = config.SCREEN_MAIN
    state.last_temp_c, state.last_rh = 22.0, 48.0
    state.graph_scale_mode = "fixed"
    warmup, count = 5, 64
    try:
        display.auto_refresh = False
        for mode, name in enumerate(("text", "big_co2", "full_graph")):
            state.display_mode = mode
            state.co2_history = [550 + (j * 83) % 1000 for j in range(config.WINDOW_SAMPLES)]
            ui.update_visibility()
            times = [0] * count
            gc.collect()
            for i in range(warmup + count):
                # Include the 3-to-4 digit layout change in Big CO2 mode.
                state.last_co2 = 960 + (i * 17) % 107
                state.co2_history.pop(0)
                state.co2_history.append(state.last_co2)
                start = time.monotonic_ns()
                ui.refresh_text()
                ui.apply_alert_colors(state.last_co2)
                if mode == 2:
                    ui.redraw_graph()
                if not display.refresh(target_frames_per_second=None, minimum_frames_per_second=0):
                    raise RuntimeError("Manual display refresh skipped")
                elapsed_us = (time.monotonic_ns() - start) // 1000
                if i >= warmup:
                    times[i - warmup] = elapsed_us
            times.sort()
            print(json.dumps({"runtime": version.CP_VERSION, "view": name, "samples": count,
                              "p50_us": times[(count - 1) // 2],
                              "p95_us": times[(95 * count + 99) // 100 - 1],
                              "max_us": times[-1]}))
    finally:
        display.auto_refresh = auto_refresh


if __name__ == "__main__":
    run()

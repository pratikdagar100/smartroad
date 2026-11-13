Drop a dashcam test video here (e.g. `road.mp4`) and run:

    run_windows.bat --camera video --source data\demo\road.mp4 --model models\pothole.onnx

With a video the GPS is simulated (explicitly, via `--gps simulated`), so potholes found in the video are placed
along the simulated route. Use it for testing the AI model, not for real mapping.

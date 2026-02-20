import cv2
import time
import os
from datetime import datetime

def burst_capture():
    # 1. Define the absolute storage path as requested
    base_dir = "/home/refrigeproject/Desktop/Frige_Project/RPi_Code/photo_set"
    
    # 2. Generate a session folder name based on the current time
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_folder = os.path.join(base_dir, f"session_{timestamp}")
    
    # Create the directory path if it doesn't exist 
    if not os.path.exists(session_folder):
        os.makedirs(session_folder, exist_ok=True)
    
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open camera.")
        return

    # Warm up camera to stabilize exposure
    for _ in range(5):
        cap.read()

    print(f"Starting 20-frame burst at 10 FPS...")
    print(f"Saving to: {session_folder}")
    
    start_time = time.time()

    for i in range(50):
        ret, frame = cap.read()
        if ret:
            # Standardized naming convention for AI team compatibility [cite: 35, 99]
            img_filename = f"{i+1:03d}.jpg"
            img_path = os.path.join(session_folder, img_filename)
            cv2.imwrite(img_path, frame)
            if (i+1) % 10 == 0:
                print(f"process: {i+1}/50")
        
        # Maintain 5 FPS (0.2s interval)
        time.sleep(0.2)

    end_time = time.time()
    total_duration = end_time - start_time
    
    print("-" * 30)
    print(f"Capture Complete.")
    print(f"Total Time: {total_duration:.2f} seconds")
    print(f"Average FPS: {20/total_duration:.2f}")
    
    cap.release()

if __name__ == "__main__":
    burst_capture()
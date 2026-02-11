import cv2
import time
import os

def burst_capture():
    
    folder_name = "fridge_burst_data"
    if not os.path.exists(folder_name):
        os.makedirs(folder_name)
    
    #
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("can't open camera")
        return

    # 3.
    print("preparing camera...")
    
    for _ in range(5):
        cap.read()

    print(f"start takin photos...")
    start_time = time.time()

    for i in range(20):
        ret, frame = cap.read()
        if ret:
            
            img_path = os.path.join(folder_name, f"{i+1:03d}.jpg")
            cv2.imwrite(img_path, frame)
            
            
            if (i+1) % 10 == 0:
                print(f"process: {i+1}/100")
        
        time.sleep(0.5)

    end_time = time.time()
    total_duration = end_time - start_time
    
    print("-" * 30)
    print(f"Done")
    print(f"total_time: {total_duration:.2f} sec")
    print(f"Avg_fps: {100/total_duration:.2f} FPS")
    print(f"save at: {os.path.abspath(folder_name)}")

    cap.release()

if __name__ == "__main__":
    burst_capture()
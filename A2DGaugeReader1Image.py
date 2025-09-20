import cv2
import numpy as np
import os
import shutil
from datetime import datetime
import tkinter as tk
from PIL import Image, ImageTk
import paho.mqtt.client as mqtt
import threading
import time

# try to import watchdog; if not available we'll fallback to polling
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except Exception:
    WATCHDOG_AVAILABLE = False

#----------------- CONFIG ----------------
watch_dir = os.getcwd()   # watch the base directory (where the script runs)
image_name = 'latest.jpg'
archive_dir = 'archive'
output_value_file = 'value.txt'
meter_min_angle = 225
meter_max_angle = 137
meter_min_value = 0
meter_max_value = 120
image_size = (500, 500)
update_interval = 1000   # ms for GUI loop
watch_poll_interval = 1.0  # seconds when using polling fallback

mqtt_broker = "russ-mckay.dyndns.org"
mqtt_port = 1886
mqtt_topic = "meter_workorder"
mqtt_payload = "Place Holder"

blur_kernel = 21
# -----------------------------------------

os.makedirs(archive_dir, exist_ok=True)

client = mqtt.Client()
try:
    client.connect(mqtt_broker, mqtt_port, 60)
    mqtt_connected = True
except Exception as e:
    print(f"MQTT connection failed: {e}")
    mqtt_connected = False

workorder_sent = False
last_archived_file = None

# state & synchronization
new_image_ready = False   # set True by watcher when latest.jpg placed
_move_lock = threading.Lock()   # protects moving/reading latest.jpg
_processed_recent = {}     # debouncing map: src_path -> timestamp

last_status = "Watcher not started yet."
safe_threshold = 25  # ✅ user-settable threshold (default 25)


def log_status(msg):
    """Print and attempt to set GUI status label (safe if GUI not yet created)."""
    global last_status
    last_status = f"{datetime.now().strftime('%H:%M:%S')} - {msg}"
    print(last_status)
    try:
        root.after(0, status_label.config, {"text": last_status})
    except Exception:
        pass


# ---------- worker that handles a discovered file ----------
def handle_new_file(src_path):
    """Waits until src_path is stable, then moves it to latest.jpg"""
    global new_image_ready

    try:
        src = os.path.abspath(src_path)
        watch_abs = os.path.abspath(watch_dir)

        if os.path.dirname(src) != watch_abs:
            return

        if not src.lower().endswith(".jpg"):
            return
        if os.path.basename(src) == image_name:
            return

        now = time.time()
        last = _processed_recent.get(src)
        if last and (now - last) < 1.5:
            return
        _processed_recent[src] = now

        log_status(f"Detected candidate: {os.path.basename(src)}")

        stable = False
        prev_size = -1
        for _ in range(40):
            try:
                size = os.path.getsize(src)
            except OSError:
                size = -1
            if size > 0 and size == prev_size:
                stable = True
                break
            prev_size = size
            time.sleep(0.25)

        if not stable:
            log_status(f"Size not stable for {os.path.basename(src)}; proceeding anyway.")

        target = os.path.join(watch_abs, image_name)

        for attempt in range(10):
            try:
                with _move_lock:
                    if os.path.exists(target):
                        try:
                            os.remove(target)
                        except Exception:
                            pass
                    shutil.move(src, target)
                    log_status(f"Moved {os.path.basename(src)} -> {image_name}")
                    new_image_ready = True
                    _processed_recent[src] = time.time()
                    return
            except Exception as e:
                log_status(f"Move attempt {attempt+1} failed: {e}")
                time.sleep(0.5)

        log_status(f"Failed to move {os.path.basename(src)} after retries.")
    except Exception as e:
        log_status(f"Watcher handler exception: {e}")


# ---------- Watcher implementation ----------
def start_watchdog():
    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            threading.Thread(target=handle_new_file, args=(event.src_path,), daemon=True).start()

        def on_moved(self, event):
            dest = getattr(event, "dest_path", None)
            if dest:
                threading.Thread(target=handle_new_file, args=(dest,), daemon=True).start()

        def on_modified(self, event):
            if event.is_directory:
                return
            threading.Thread(target=handle_new_file, args=(event.src_path,), daemon=True).start()

    observer = Observer()
    observer.schedule(Handler(), watch_dir, recursive=False)
    observer.start()
    log_status(f"watchdog watching {watch_dir} for .jpg")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def start_poller():
    log_status(f"polling {watch_dir} for .jpg (watchdog unavailable)")
    while True:
        try:
            for fname in os.listdir(watch_dir):
                if not fname.lower().endswith(".jpg"):
                    continue
                if fname == image_name:
                    continue
                src = os.path.join(watch_dir, fname)
                threading.Thread(target=handle_new_file, args=(src,), daemon=True).start()
            time.sleep(watch_poll_interval)
        except Exception as e:
            log_status(f"Poller exception: {e}")
            time.sleep(watch_poll_interval)


if WATCHDOG_AVAILABLE:
    threading.Thread(target=start_watchdog, daemon=True).start()
else:
    threading.Thread(target=start_poller, daemon=True).start()


# -------- Image processing --------
def process_image():
    global last_archived_file, new_image_ready

    if not new_image_ready:
        return None, None

    target = os.path.join(watch_dir, image_name)

    with _move_lock:
        if not os.path.exists(target):
            return None, None

        img = cv2.imread(target)
        if img is None:
            log_status("OpenCV failed to read latest.jpg")
            return None, None

        img = cv2.resize(img, image_size)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5,5), 0)
        edges = cv2.Canny(blur, 50, 150)
        lines = cv2.HoughLinesP(edges,1,np.pi/180,threshold=80,minLineLength=80,maxLineGap=20)

        val = -1
        if lines is not None:
            longest_line = max(
                lines,
                key=lambda line: np.linalg.norm([line[0][0]-line[0][2], line[0][1]-line[0][3]])
            )
            x1,y1,x2,y2 = longest_line[0]
            cv2.line(img,(x1,y1),(x2,y2),(0,0,255),2)

            center = (img.shape[1]//2,img.shape[0]//2)
            d1 = np.linalg.norm([x1-center[0],y1-center[1]])
            d2 = np.linalg.norm([x2-center[0],y2-center[1]])
            needle_end = (x1,y1) if d1>d2 else (x2,y2)
            needle_vector = np.array([needle_end[0]-center[0], center[1]-needle_end[1]])
            angle_rad = np.arctan2(needle_vector[1], needle_vector[0])
            angle_deg = (90 - np.degrees(angle_rad)) % 360
            arc_span = 360 - meter_min_angle + meter_max_angle
            if angle_deg >= meter_min_angle:
                val = (angle_deg - meter_min_angle)*(meter_max_value-meter_min_value)/arc_span
            else:
                val = (angle_deg + (360 - meter_min_angle))*(meter_max_value-meter_min_value)/arc_span
            val = round(np.clip(val,meter_min_value,meter_max_value),2)

        # Draw green box around image
        cv2.rectangle(img, (0,0), (img.shape[1]-1, img.shape[0]-1), (0,255,0), 5)

        try:
            with open(output_value_file, "w") as f:
                f.write(str(val))
        except Exception as e:
            log_status(f"Failed writing {output_value_file}: {e}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_filename = f"latest_{val}_{timestamp}.jpg"
        archive_path = os.path.join(archive_dir, archive_filename)
        last_archived_file = archive_path
        try:
            shutil.move(target, archive_path)
            log_status(f"Archived as {archive_filename}")
        except Exception as e:
            log_status(f"Archiving failed: {e}")

        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        tk_img = ImageTk.PhotoImage(pil_img)

        new_image_ready = False
        return val, tk_img


# -------- GUI + callbacks --------
def update_gui():
    global workorder_sent, safe_threshold
    val, tk_img = process_image()
    if val is not None:
        value_label.config(text=f"Meter Value: {val}")
        image_label.config(image=tk_img)
        image_label.image = tk_img

        if val < 15 or val > 66:
            notify_label.config(
                text="❗ Reading appears to be inaccurate\nReading outside expected range.\n"
                     "This could be due to shadows or lighting issues.\nMove camera position and try again.",
                fg="red", justify="center")
            clear_button.config(state="normal")
        elif val <= safe_threshold:  # ✅ use user-defined threshold
            notify_label.config(text=f"Temperature OK ✅ (≤ {safe_threshold})", fg="green")
            workorder_sent = False
            clear_button.config(state="disabled")
        else:
            msg = "Warning! Value exceeds safe working temperature ⚠️"
            color = "red"
            if mqtt_connected and not workorder_sent:
                try:
                    client.publish(mqtt_topic, val)
                    msg += "\nInspection work order generated in Maximo"
                    color = "blue"
                    workorder_sent = True
                except Exception as e:
                    msg += f"\nMQTT publish failed: {e}"
                    color = "orange"
            notify_label.config(text=msg, fg=color, justify="center")
            clear_button.config(state="normal")


    root.after(update_interval, update_gui)


def clear_alert():
    global workorder_sent
    workorder_sent = False
    notify_label.config(text="Alerts cleared. Monitoring resumed...", fg="black")
    clear_button.config(state="disabled")


def conversion_good():
    global last_archived_file
    if last_archived_file and os.path.exists(last_archived_file):
        base, ext = os.path.splitext(last_archived_file)
        new_name = f"{base}_GOOD{ext}"
        os.rename(last_archived_file, new_name)
        last_archived_file = new_name
        log_status("File marked as GOOD ✅")


def conversion_bad():
    global last_archived_file
    if last_archived_file and os.path.exists(last_archived_file):
        base, ext = os.path.splitext(last_archived_file)
        new_name = f"{base}_BAD{ext}"
        os.rename(last_archived_file, new_name)
        last_archived_file = new_name
        log_status("File marked as BAD ❌")


def on_exit():
    root.destroy()


def set_threshold():
    global safe_threshold
    try:
        safe_threshold = float(threshold_entry.get())
        notify_label.config(text=f"Threshold set to {safe_threshold}", fg="black")
    except ValueError:
        notify_label.config(text="Invalid threshold value ❌", fg="red")


# -------- GUI Setup --------
root = tk.Tk()
root.title("Analogue Meter Reader")
root.geometry("1000x950")

title_label = tk.Label(root, text="Analogue Meter Reader", font=("Arial", 20, "bold"))
title_label.pack(pady=10)

value_label = tk.Label(root, text="Waiting for image...", font=("Arial", 18))
value_label.pack(pady=10)

image_label = tk.Label(root)
image_label.pack(pady=10)

notify_label = tk.Label(root, text="System idle...", font=("Arial",16), justify="center")
notify_label.pack(pady=15)

# status label to show watcher activity
status_label = tk.Label(root, text=last_status, font=("Arial",10), justify="left")
status_label.pack(pady=5)

clear_button = tk.Button(root, text="Clear Alert", font=("Arial",14,"bold"),
                         bg="lightgray", fg="black", state="disabled", command=clear_alert)
clear_button.pack(pady=5)

# Frame for threshold setting (left-justified)
threshold_frame = tk.Frame(root)
threshold_frame.pack(pady=5, anchor="w")  # left justify

threshold_label = tk.Label(threshold_frame, text="Safe Threshold:", font=("Arial",14))
threshold_label.pack(side="left", padx=5)

threshold_entry = tk.Entry(threshold_frame, font=("Arial",14), width=6)
threshold_entry.insert(0, str(safe_threshold))
threshold_entry.pack(side="left", padx=5)

set_threshold_btn = tk.Button(threshold_frame, text="Set Threshold",
                              font=("Arial",12,"bold"), command=set_threshold)
set_threshold_btn.pack(side="left", padx=5)

# Frame for Good/Bad conversion (left-justified)
conversion_frame = tk.Frame(root)
conversion_frame.pack(pady=5, anchor="w")  # left justify

optional_conversion_label = tk.Label(conversion_frame,
                                     text="Optional: mark images for improved performance",
                                     font=("Arial", 12, "italic"))
optional_conversion_label.pack(side="left", padx=5)

question_label = tk.Label(conversion_frame, text="Is returned number accurate?", font=("Arial",14))
question_label.pack(side="left", padx=5)

conversion_good_btn = tk.Button(conversion_frame, text="✔ Good", font=("Arial",14,"bold"),
                                bg="white", fg="black", width=12, command=conversion_good)
conversion_good_btn.pack(side="left", padx=5)

conversion_bad_btn = tk.Button(conversion_frame, text="❌ Bad", font=("Arial",14,"bold"),
                               bg="white", fg="black", width=12, command=conversion_bad)
conversion_bad_btn.pack(side="left", padx=5)

exit_button = tk.Button(root, text="Exit", font=("Arial",16,"bold"),
                        bg="white", fg="black", command=on_exit)
exit_button.pack(side="bottom", fill="x", padx=20, pady=20)

# start GUI loop
root.after(1000, update_gui)
root.mainloop()

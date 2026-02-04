import subprocess
import time
import os
import sys
import datetime
import getpass
import re
import logging

# ================= 設定區 =================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
INVENTORY_PATH = os.path.join(PROJECT_ROOT, "deploy", "inventory.ini")
PLAYBOOK_DIR = os.path.join(SCRIPT_DIR, "playbooks")
DATA_LAKE_DIR = "/mnt/d/Traffic_Data"
LOG_FILE = os.path.join(SCRIPT_DIR, "pipeline_debug.log")

SERVICE_NAME = "my-simulation_traffic-bot"

DEFAULT_THRESHOLD_GB = 2.0 
DEFAULT_MAX_ROUNDS = 1
DEFAULT_TARGET_REPLICAS = 50

THRESHOLD_GB = DEFAULT_THRESHOLD_GB
MAX_ROUNDS = DEFAULT_MAX_ROUNDS
TARGET_REPLICAS = DEFAULT_TARGET_REPLICAS
SUDO_PASSWORD = os.getenv('ANSIBLE_BECOME_PASS', "")

# Logger 設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
# ==========================================

def get_input_value(prompt, default_val, cast_type=str):
    try:
        user_input = input(f"{prompt} [預設 {default_val}]: ").strip()
        if not user_input: return default_val
        return cast_type(user_input)
    except ValueError:
        logging.error("輸入格式錯誤！")
        sys.exit(1)

def run_cmd(cmd, shell=False, check=True):
    """一般指令執行"""
    try:
        result = subprocess.run(cmd, shell=shell, check=check, 
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

def run_cmd_stream(cmd, description="Executing", timeout=900):
    """串流指令執行 (雙重輸出 + Timeout + 顯示警告)"""
    logging.info(f"--- [{description}] Start ---")
    start_time = time.time()
    
    try:
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        for line in process.stdout:
            line = line.strip()
            if not line: continue

            # 寫入檔案
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now()} | {line}\n")

            # 螢幕顯示邏輯
            if "TASK [" in line:
                try: task_name = line.split('TASK [')[-1].split(']')[0]
                except IndexError: task_name = line
                
                # 包含 "debug" 但不包含 "警告" 的才隱藏，這樣警告訊息會顯示
                if "debug" not in task_name.lower() or "警告" in task_name:
                    print(f"      -> Step: {task_name}")
            
            elif "changed:" in line or "ok:" in line:
                if "[" in line:
                    host = line.split('[')[1].split(']')[0]
                    status = "Changed" if "changed" in line else "OK"
                    print(f"      -> {host}: {status}")

            elif any(k in line.lower() for k in ["fatal:", "failed:", "error", "unreachable"]):
                print(f"      [!] ERROR: {line}")

            # 捕捉 Ansible debug msg
            elif "\"msg\":" in line:
                msg_content = line.replace('"msg":', '').strip().strip('"')
                print(f"      [!] Message: {msg_content}")

            if (time.time() - start_time) > timeout:
                process.kill()
                raise TimeoutError("Ansible task timed out")

        return_code = process.wait()
        logging.info(f"--- [{description}] End (RC={return_code}) ---")
        return return_code == 0 

    except Exception as e:
        logging.error(f"Exception during execution: {e}")
        return False

def get_ansible_base_cmd(hosts_pattern, module="shell", args=None):
    cmd = ["ansible", hosts_pattern, "-i", INVENTORY_PATH, "-b",
           "--extra-vars", f"ansible_become_pass={SUDO_PASSWORD}"]
    if module: cmd.extend(["-m", module])
    if args: cmd.extend(["-a", args])
    return cmd

def get_playbook_cmd(playbook_path, extra_vars_dict=None):
    """
    [修改] 支援傳入額外變數 (extra_vars_dict)
    """
    vars_str = f"ansible_become_pass={SUDO_PASSWORD}"
    
    if extra_vars_dict:
        for key, value in extra_vars_dict.items():
            vars_str += f" {key}={value}"

    return ["ansible-playbook", "-i", INVENTORY_PATH, playbook_path,
            "--extra-vars", vars_str]

def get_max_file_size_gb():
    cmd = get_ansible_base_cmd("workers", args="stat -c %s /tmp/traffic_data/*.pcap 2>/dev/null || echo 0")
    output = run_cmd(cmd, check=False)
    max_size = 0.0
    if output:
        for line in output.split('\n'):
            line = line.strip()
            if line.isdigit():
                size_gb = int(line) / (1024**3)
                if size_gb > max_size: max_size = size_gb
    return max_size

def ensure_service_scale(target_replicas, max_retries=5):
    logging.info(f"Enforcing service scale to {target_replicas}...")
    scale_cmd = get_ansible_base_cmd("managers", args=f"docker service scale {SERVICE_NAME}={target_replicas}")
    check_cmd = get_ansible_base_cmd("managers", args=f"docker service ls --filter name={SERVICE_NAME}")

    for i in range(max_retries):
        run_cmd(scale_cmd, check=False)
        output = run_cmd(check_cmd, check=False)
        if output:
            match = re.search(r'\s(\d+)/(\d+)', output)
            if match:
                desired = int(match.group(2))
                if desired == target_replicas:
                    logging.info(f"Command accepted by Manager (Desired={desired}).")
                    return True
                else:
                    logging.warning(f"Scale command sent but ignored (Desired={desired}). Retrying...")
        time.sleep(3)
    logging.error("Failed to enforce scale command after multiple retries!")
    return False

def verify_service_status(target_replicas, retry_times=6):
    logging.info(f"Waiting for containers to reach state (Target: {target_replicas})...")
    cmd = get_ansible_base_cmd("managers", args=f"docker service ls --filter name={SERVICE_NAME}")
    for i in range(retry_times):
        output = run_cmd(cmd, check=False)
        if output:
            match = re.search(r'\s(\d+)/(\d+)', output)
            if match:
                current = int(match.group(1))
                desired = int(match.group(2))
                print(f"      -> Attempt {i+1}/{retry_times}: {current}/{desired}")
                if target_replicas > 0:
                    if current >= target_replicas: return True
                else:
                    if current == 0: return True
        time.sleep(5)
    return False

def verify_capture_status(retry_times=3):
    logging.info("Verifying tcpdump status...")
    cmd = get_ansible_base_cmd("workers", args="pgrep -f tcpdump || true")
    for i in range(retry_times):
        if run_cmd(cmd, check=True): return True
        time.sleep(2)
    return False

def cleanup_on_exit():
    logging.warning("!!! INTERRUPTED !!! Performing EMERGENCY CLEANUP...")
    try: run_cmd(get_ansible_base_cmd("managers", args=f"docker service scale {SERVICE_NAME}=0"), check=False)
    except: pass
    try: run_cmd(get_ansible_base_cmd("workers", args="pkill tcpdump || true"), check=False)
    except: pass
    try: run_cmd_stream(get_playbook_cmd(os.path.join(PLAYBOOK_DIR, "stop_and_fetch.yml")), description="Emergency Fetch")
    except: pass
    logging.info("!!! CLEANUP COMPLETE !!!")

def main():
    global SUDO_PASSWORD, THRESHOLD_GB, MAX_ROUNDS, TARGET_REPLICAS
    print(f"=== Auto-Traffic-Pipeline Started ===")
    
    if not SUDO_PASSWORD:
        try: SUDO_PASSWORD = getpass.getpass("1. 請輸入 sudo 密碼: ")
        except: sys.exit(1)
    if not SUDO_PASSWORD: sys.exit(1)

    print("\n--- 設定參數 ---")
    THRESHOLD_GB = get_input_value("2. 檔案上限 (GB)", DEFAULT_THRESHOLD_GB, float)
    MAX_ROUNDS = get_input_value("3. 輪數", DEFAULT_MAX_ROUNDS, int)
    TARGET_REPLICAS = get_input_value("4. 容器數", DEFAULT_TARGET_REPLICAS, int)
    
    os.makedirs(DATA_LAKE_DIR, exist_ok=True)

    try:
        for round_id in range(1, MAX_ROUNDS + 1):
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            logging.info(f">>> [Round {round_id}/{MAX_ROUNDS}] Start Time: {timestamp}")

            # 1. 啟動流量
            if not ensure_service_scale(TARGET_REPLICAS): raise RuntimeError("Scale command failed")
            if not verify_service_status(target_replicas=TARGET_REPLICAS, retry_times=60): 
                raise RuntimeError("Containers failed to start")

            # 2. 啟動錄製
            logging.info("Starting tcpdump...")
            run_cmd(get_playbook_cmd(os.path.join(PLAYBOOK_DIR, "start_capture.yml")))
            if not verify_capture_status(): raise RuntimeError("Capture failed")
            
            # 3. 監控
            logging.info("Recording traffic...")
            start_time = time.time()
            while True:
                max_gb = get_max_file_size_gb()
                elapsed = int(time.time() - start_time)
                sys.stdout.write(f"\r      -> Max File Size: {max_gb:.4f} GB / {THRESHOLD_GB} GB (Elapsed: {elapsed}s)")
                sys.stdout.flush()
                if max_gb >= THRESHOLD_GB:
                    print("\n")
                    logging.info("Threshold reached!")
                    break
                time.sleep(10)

            # 4. 停止流量
            if not ensure_service_scale(0): raise RuntimeError("Stop command failed")
            verify_service_status(target_replicas=0, retry_times=60)
            print("      -> Waiting 10s for connections to close...")
            time.sleep(60)

            # 5. 全域停止 Tcpdump
            logging.info("Stopping tcpdump globally...")
            run_cmd(get_ansible_base_cmd("workers", args="pkill tcpdump || true"))
            time.sleep(5)

            # 6. Fetch (Serial + Retry + Last Resort Cleanup)
            logging.info("Fetching files...")
            fetch_success = False
            max_retries = 3
            
            for attempt in range(max_retries):
                # 判斷是否為最後一次嘗試
                is_last_attempt = (attempt == max_retries - 1)
                
                # 如果是最後一次，傳入 force_cleanup=yes
                current_vars = {"force_cleanup": "yes"} if is_last_attempt else {"force_cleanup": "no"}

                if attempt > 0:
                    logging.warning(f"Retry transfer in 20s (Attempt {attempt+1}/{max_retries})...")
                    time.sleep(20)
                
                if is_last_attempt:
                    logging.warning("!!! LAST ATTEMPT: Files will be deleted even if transfer fails !!!")

                # 執行 Playbook 並帶入變數
                cmd = get_playbook_cmd(os.path.join(PLAYBOOK_DIR, "stop_and_fetch.yml"), extra_vars_dict=current_vars)
                
                if run_cmd_stream(cmd, description=f"Downloading Data (Attempt {attempt+1})", timeout=3600):
                    logging.info("Transfer process completed successfully.")
                    fetch_success = True
                    break
                else:
                    logging.warning("This transfer attempt failed.")
            
            if not fetch_success: 
                logging.error("All transfer attempts failed! (Remote files cleaned up)")

            # 7. Rename
            logging.info(f"Organizing files...")
            count = 0
            if os.path.exists(DATA_LAKE_DIR):
                files = os.listdir(DATA_LAKE_DIR)
                for filename in files:
                    if filename.endswith(".pcap") and not filename.startswith("round"):
                        try:
                            new_name = f"round{round_id}_{timestamp}_{filename}"
                            os.rename(os.path.join(DATA_LAKE_DIR, filename), os.path.join(DATA_LAKE_DIR, new_name))
                            logging.info(f"Renamed: {filename} -> {new_name}")
                            count += 1
                        except OSError: pass
            
            logging.info(f"Round {round_id} completed. {count} files processed.")
            print("      -> Cooling down for 10s...")
            time.sleep(10)

        logging.info("All Rounds Completed Successfully.")

    except KeyboardInterrupt:
        print("\n")
        cleanup_on_exit()
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        cleanup_on_exit()
        sys.exit(1)

if __name__ == "__main__":
    main()
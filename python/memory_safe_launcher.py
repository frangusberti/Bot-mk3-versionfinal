import subprocess
import psutil
import time
import sys
import os

# Configuration
PROC_COMMAND = ["python", "python/ppo_vnext_p3_5.py"]
RSS_LIMIT_MB = 8192 # Hard limit 8GB
CHECK_INTERVAL_SEC = 5

def launch_guarded():
    print(f"[LAUNCHER] Starting process: {' '.join(PROC_COMMAND)}")
    print(f"[LAUNCHER] Hard Limit: {RSS_LIMIT_MB} MB")
    
    # Forward arguments
    cmd = PROC_COMMAND + sys.argv[1:]
    
    process = subprocess.Popen(cmd)
    p_util = psutil.Process(process.pid)
    
    try:
        while process.poll() is None:
            try:
                # Check RSS of process and all its children
                rss_total = p_util.memory_info().rss
                for child in p_util.children(recursive=True):
                    rss_total += child.memory_info().rss
                
                rss_mb = rss_total / 1024 / 1024
                
                if rss_mb > RSS_LIMIT_MB:
                    print(f"\n[CRITICAL] Hard Memory Limit Exceeded ({rss_mb:.1f}MB > {RSS_LIMIT_MB}MB)")
                    print("[CRITICAL] Terminating process tree to prevent OOM...")
                    for child in p_util.children(recursive=True):
                        child.kill()
                    process.kill()
                    sys.exit(1)
                
            except psutil.NoSuchProcess:
                break
                
            time.sleep(CHECK_INTERVAL_SEC)
            
    except KeyboardInterrupt:
        print("[LAUNCHER] Interrupted by user.")
        process.terminate()
        
    print(f"[LAUNCHER] Process exited with code {process.returncode}")

if __name__ == "__main__":
    launch_guarded()

import os
import subprocess
import sys
import customtkinter

def build():
    # Find customtkinter directory
    ctk_dir = os.path.dirname(customtkinter.__file__)
    
    # Path of main script
    main_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    
    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",        # Compile into a single executable file
        "--windowed",       # Do not open console window
        f"--add-data={ctk_dir};customtkinter/",  # Include customtkinter resources
        "--add-binary=ffmpeg.exe;.",            # Bundle ffmpeg binary
        "--add-binary=ffprobe.exe;.",           # Bundle ffprobe binary
        "--name=YT_Music_Downloader",
        main_script
    ]
    
    print("Executing PyInstaller compilation...")
    print("Command:", " ".join(cmd))
    
    try:
        subprocess.run(cmd, check=True)
        print("\nSUCCESS! Standalone executable generated at: dist/YT_Music_Downloader.exe")
    except subprocess.CalledProcessError as e:
        print("\nERROR: Compilation failed.", file=sys.stderr)
        sys.exit(e.returncode)

if __name__ == "__main__":
    build()

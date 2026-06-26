import os
import sys
import shutil
import zipfile
import requests

def get_app_dir():
    # If packaged with PyInstaller, the executable runs from its folder, but sys.executable is the EXE
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def is_ffmpeg_available():
    # Check MEIPASS first (if frozen by PyInstaller)
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        meipass_ffmpeg = os.path.join(sys._MEIPASS, "ffmpeg.exe")
        meipass_ffprobe = os.path.join(sys._MEIPASS, "ffprobe.exe")
        if os.path.exists(meipass_ffmpeg) and os.path.exists(meipass_ffprobe):
            return True

    # Check local directory
    app_dir = get_app_dir()
    local_ffmpeg = os.path.join(app_dir, "ffmpeg.exe")
    local_ffprobe = os.path.join(app_dir, "ffprobe.exe")
    if os.path.exists(local_ffmpeg) and os.path.exists(local_ffprobe):
        return True
    
    # Check system PATH
    ffmpeg_in_path = shutil.which("ffmpeg")
    ffprobe_in_path = shutil.which("ffprobe")
    if ffmpeg_in_path and ffprobe_in_path:
        return True
        
    return False

def get_ffmpeg_dir():
    # Check MEIPASS first (if frozen by PyInstaller)
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        meipass_ffmpeg = os.path.join(sys._MEIPASS, "ffmpeg.exe")
        if os.path.exists(meipass_ffmpeg):
            return sys._MEIPASS

    app_dir = get_app_dir()
    local_ffmpeg = os.path.join(app_dir, "ffmpeg.exe")
    if os.path.exists(local_ffmpeg):
        return app_dir
    
    # Check path
    ffmpeg_in_path = shutil.which("ffmpeg")
    if ffmpeg_in_path:
        return os.path.dirname(ffmpeg_in_path)
        
    return None

def download_ffmpeg(progress_callback=None):
    """
    Downloads FFmpeg static essentials build and extracts ffmpeg.exe and ffprobe.exe
    to the app directory.
    progress_callback: function that takes (percent_downloaded: float, status_text: str)
    """
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    app_dir = get_app_dir()
    zip_path = os.path.join(app_dir, "ffmpeg.zip")
    
    try:
        if progress_callback:
            progress_callback(0, "Connecting to download server...")
            
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        if progress_callback:
                            progress_callback(percent, f"Downloading: {percent:.1f}% ({downloaded / (1024*1024):.1f}MB / {total_size / (1024*1024):.1f}MB)")
                    else:
                        if progress_callback:
                            progress_callback(-1, f"Downloading... ({downloaded / (1024*1024):.1f}MB downloaded)")
                            
        if progress_callback:
            progress_callback(100, "Extracting files...")
            
        # Extract files
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Find ffmpeg.exe and ffprobe.exe inside the zip (they are inside a subfolder like ffmpeg-release-essentials/bin/)
            extracted_count = 0
            for file_info in zip_ref.infolist():
                filename = os.path.basename(file_info.filename)
                if filename in ["ffmpeg.exe", "ffprobe.exe"]:
                    # Extract directly to app_dir without the subdirectories
                    source = zip_ref.open(file_info)
                    target_path = os.path.join(app_dir, filename)
                    with open(target_path, "wb") as target_file:
                        shutil.copyfileobj(source, target_file)
                    extracted_count += 1
                    
        # Remove zip file
        if os.path.exists(zip_path):
            os.remove(zip_path)
            
        if extracted_count >= 2:
            if progress_callback:
                progress_callback(100, "Done!")
            return True
        else:
            if progress_callback:
                progress_callback(-1, "Extraction failed: ffmpeg binaries not found in zip.")
            return False
            
    except Exception as e:
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except:
                pass
        if progress_callback:
            progress_callback(-1, f"Error: {str(e)}")
        return False

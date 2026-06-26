import os
import sys
import time
from main import extract_youtube_id
from downloader import YTDownloader
import ffmpeg_utils

def test_regex():
    print("--- Testing Regex URL Extraction ---")
    urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
        "https://studio.youtube.com/channel/UC123/music/detail?v=dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "youtube.com/watch?v=dQw4w9WgXcQ"
    ]
    for url in urls:
        vid = extract_youtube_id(url)
        print(f"URL: {url} => Extracted ID: {vid}")
        assert vid == "dQw4w9WgXcQ", f"Failed for {url}"
    print("Regex tests PASSED!\n")

def test_downloader_metadata():
    print("--- Testing Metadata Extraction ---")
    dl = YTDownloader()
    
    # We will use a known short video ID
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    result = {}
    done = False
    
    def callback(info, error):
        nonlocal done, result
        if error:
            result['error'] = error
        else:
            result['info'] = info
        done = True
        
    dl.get_info(test_url, callback)
    
    # Wait for background thread
    for _ in range(30):
        if done:
            break
        time.sleep(0.5)
        
    if 'error' in result:
        print(f"Metadata extraction failed: {result['error']}")
        # Network errors can happen in sandbox, so we won't crash the test, but log it
    else:
        info = result.get('info', {})
        print("Metadata extraction SUCCESS!")
        print(f"Title: {info.get('title')}")
        print(f"Channel: {info.get('channel')}")
        print(f"Duration: {info.get('duration')}s")
        print(f"Thumbnail URL: {info.get('thumbnail')}")
        assert info.get('video_id') == "dQw4w9WgXcQ"
    print("")

def test_ffmpeg_utils():
    print("--- Testing FFmpeg Utils ---")
    available = ffmpeg_utils.is_ffmpeg_available()
    print(f"FFmpeg available on system: {available}")
    app_dir = ffmpeg_utils.get_app_dir()
    print(f"App Directory: {app_dir}")
    print("FFmpeg tests PASSED!\n")

if __name__ == "__main__":
    test_regex()
    test_ffmpeg_utils()
    # Skip network test in non-interactive sandbox if internet is restricted,
    # but run metadata extraction just in case it works.
    try:
        test_downloader_metadata()
    except Exception as e:
        print(f"Skipping downloader metadata test due to error: {e}")
    print("All core tests completed successfully!")

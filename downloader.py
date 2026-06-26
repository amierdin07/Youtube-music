import os
import threading
import yt_dlp
from concurrent.futures import ThreadPoolExecutor

class DownloadPausedException(Exception):
    pass

class YTDownloader:
    def __init__(self, cookies_browser=None):
        self.cookies_browser = cookies_browser.lower() if cookies_browser and cookies_browser != "None" else None
        self.executor = ThreadPoolExecutor(max_workers=3) # Limit parallel downloads to 3
        self.info_executor = ThreadPoolExecutor(max_workers=2) # Limit parallel search/info threads to 2 to avoid YouTube rate-limiting
        self.paused_videos = set()
        
    def set_cookies_browser(self, browser):
        self.cookies_browser = browser.lower() if browser and browser != "None" else None
        
    def pause_video(self, video_id):
        self.paused_videos.add(video_id)

    def resume_video(self, video_id):
        if video_id in self.paused_videos:
            self.paused_videos.remove(video_id)
    def _parse_and_callback(self, info, callback):
        if info and 'entries' in info:
            if info['entries']:
                info = info['entries'][0]
            else:
                raise Exception("No search results found.")
                
        title = info.get('title', 'Unknown Title')
        channel = info.get('uploader', 'Unknown Channel')
        duration = info.get('duration', 0)
        thumbnail = info.get('thumbnail', '')
        video_id = info.get('id', '')
        
        callback({
            'url': f"https://www.youtube.com/watch?v={video_id}",
            'video_id': video_id,
            'title': title,
            'channel': channel,
            'duration': duration,
            'thumbnail': thumbnail,
            'status': 'Queued'
        }, None)

    def get_info(self, url, callback):
        """
        Extract video information in a background thread and call callback(info, error).
        Supports direct video URLs and ytsearch1: queries.
        """
        def task():
            opts = {
                'skip_download': True,
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 10,
                'retries': 0,
            }
            if self.cookies_browser:
                opts['cookiesfrombrowser'] = (self.cookies_browser,)
                
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    self._parse_and_callback(info, callback)
            except Exception as e:
                err_str = str(e)
                # Catch cookie lock error and fallback to None (no cookies)
                if self.cookies_browser and ("cookie" in err_str.lower() or "permission denied" in err_str.lower() or "locked" in err_str.lower() or "dpapi" in err_str.lower() or "decrypt" in err_str.lower()):
                    opts_no_cookies = opts.copy()
                    if 'cookiesfrombrowser' in opts_no_cookies:
                        del opts_no_cookies['cookiesfrombrowser']
                    try:
                        with yt_dlp.YoutubeDL(opts_no_cookies) as ydl:
                            info = ydl.extract_info(url, download=False)
                            self._parse_and_callback(info, callback)
                    except Exception as e2:
                        callback(None, str(e2))
                else:
                    callback(None, err_str)
                
        self.info_executor.submit(task)

    def download_track(self, track_info, download_dir, output_format, ffmpeg_path, progress_callback, completion_callback, video_resolution=None):
        """
        Downloads a single track in a background thread.
        output_format: 'M4A', 'MP3', or 'MP4'
        progress_callback: function(video_id, percent, speed, eta, status)
        completion_callback: function(video_id, success, error_message)
        """
        def task():
            video_id = track_info['video_id']
            url = track_info['url']
            
            # Setup yt-dlp options
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
                'socket_timeout': 15,
                'retries': 2,
                'concurrent_fragment_downloads': 8, # IDM-like multi-connection speed
            }
            
            if self.cookies_browser:
                ydl_opts['cookiesfrombrowser'] = (self.cookies_browser,)

            # Handle format selection
            if output_format == 'MP3':
                ydl_opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '320',
                    }],
                })
                if ffmpeg_path:
                    ydl_opts['ffmpeg_location'] = ffmpeg_path
            elif output_format == 'MP4':
                height = ""
                if video_resolution and video_resolution != "Best Quality":
                    height = video_resolution.replace("p", "")
                
                if ffmpeg_path:
                    res_limit = f"[height<={height}]" if height else ""
                    ydl_opts.update({
                        'format': f'bestvideo{res_limit}+bestaudio/best{res_limit}',
                        'merge_output_format': 'mp4',
                        'ffmpeg_location': ffmpeg_path
                    })
                else:
                    res_limit = f"[height<={height}]" if height else ""
                    ydl_opts.update({
                        'format': f'bestvideo{res_limit}[ext=mp4]+bestaudio[ext=m4a]/best{res_limit}[ext=mp4]/best',
                    })
            else: # M4A
                ydl_opts.update({
                    'format': 'bestaudio[ext=m4a]/bestaudio/best',
                })
                
            def ydl_progress_hook(d):
                # Check for pause signal
                if video_id in self.paused_videos:
                    raise DownloadPausedException("Paused by user")
                    
                if d['status'] == 'downloading':
                    downloaded = d.get('downloaded_bytes', 0)
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    percent = 0.0
                    if total > 0:
                        percent = (downloaded / total) * 100
                    
                    speed = d.get('speed', 0)
                    speed_str = ""
                    if speed:
                        if speed > 1024 * 1024:
                            speed_str = f"{speed / (1024*1024):.1f} MB/s"
                        else:
                            speed_str = f"{speed / 1024:.1f} KB/s"
                            
                    eta = d.get('eta', 0)
                    eta_str = ""
                    if eta:
                        m, s = divmod(eta, 60)
                        eta_str = f"{m}m {s}s" if m > 0 else f"{s}s"
                        
                    progress_callback(video_id, percent, speed_str, eta_str, 'Downloading')
                    
                elif d['status'] == 'finished':
                    progress_callback(video_id, 100.0, "", "", 'Converting' if output_format == 'MP3' else 'Finalizing')

            ydl_opts['progress_hooks'] = [ydl_progress_hook]

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                completion_callback(video_id, True, None)
            except DownloadPausedException:
                completion_callback(video_id, False, "Paused")
            except Exception as e:
                err_str = str(e)
                # Catch cookie lock error and fallback to None (no cookies)
                if self.cookies_browser and ("cookie" in err_str.lower() or "permission denied" in err_str.lower() or "locked" in err_str.lower() or "dpapi" in err_str.lower() or "decrypt" in err_str.lower()):
                    ydl_opts_no_cookies = ydl_opts.copy()
                    if 'cookiesfrombrowser' in ydl_opts_no_cookies:
                        del ydl_opts_no_cookies['cookiesfrombrowser']
                    try:
                        progress_callback(video_id, 0, "Retrying without cookies...", "", 'Downloading')
                        with yt_dlp.YoutubeDL(ydl_opts_no_cookies) as ydl:
                            ydl.download([url])
                        completion_callback(video_id, True, None)
                    except DownloadPausedException:
                        completion_callback(video_id, False, "Paused")
                    except Exception as e2:
                        completion_callback(video_id, False, str(e2))
                else:
                    completion_callback(video_id, False, err_str)
                    
        self.executor.submit(task)


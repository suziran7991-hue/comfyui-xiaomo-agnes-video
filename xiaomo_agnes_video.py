import requests
import time
import json
import tempfile
import os
import torch
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import cv2
import urllib3
import subprocess
import shutil
import logging
import imageio
from typing import Optional, Dict, Any, List, Tuple
import asyncio
import sys
import pickle
import warnings
from pathlib import Path
import re
import glob

# ====================== 【最彻底的 Windows 10054 错误抑制】 ======================
warnings.filterwarnings("ignore", message=".*ProactorBasePipeTransport.*")
warnings.filterwarnings("ignore", message=".*ConnectionResetError.*")
warnings.filterwarnings("ignore", category=ResourceWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

def ignore_connection_reset(exc_type, exc_val, exc_tb):
    if exc_type is ConnectionResetError and ("10054" in str(exc_val) or "远程主机强迫关闭" in str(exc_val)):
        return
    sys.__excepthook__(exc_type, exc_val, exc_tb)
sys.excepthook = ignore_connection_reset

def asyncio_exception_handler(loop, context):
    exc = context.get('exception')
    if isinstance(exc, ConnectionResetError) and ("10054" in str(exc) or "远程主机强迫关闭" in str(exc)):
        return
    loop.default_exception_handler(context)
try:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(asyncio_exception_handler)
except RuntimeError:
    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(asyncio_exception_handler)
    except:
        pass
# =================================================================================

logger = logging.getLogger("XiaoMoAgnesVideo")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_current_node_unique_id = None
def set_current_node_id(unique_id):
    global _current_node_unique_id
    _current_node_unique_id = unique_id

def update_frontend_progress(percent: float):
    try:
        percent = max(0.0, min(100.0, float(percent)))
        if _current_node_unique_id is None: return
        import comfy.execution
        exec_obj = comfy.execution.current_execution.get()
        if exec_obj and hasattr(exec_obj, 'set_progress'):
            exec_obj.set_progress(_current_node_unique_id, int(percent))
    except Exception: pass

_ffmpeg_path_cache = None
def find_ffmpeg() -> Optional[str]:
    global _ffmpeg_path_cache
    if _ffmpeg_path_cache is not None: return _ffmpeg_path_cache if _ffmpeg_path_cache else None
    ffmpeg_exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    system_ffmpeg = shutil.which(ffmpeg_exe)
    if system_ffmpeg:
        _ffmpeg_path_cache = system_ffmpeg
        logger.info(f"【FFmpeg检测】找到系统FFmpeg: {system_ffmpeg}")
        return system_ffmpeg
    try:
        current_dir = Path(__file__).resolve().parent
        for _ in range(6):
            for candidate in [
                current_dir / ffmpeg_exe, current_dir / "bin" / ffmpeg_exe,
                current_dir / "python_embeded" / ffmpeg_exe,
                current_dir / "python_embeded" / "Scripts" / ffmpeg_exe
            ]:
                if candidate.exists():
                    _ffmpeg_path_cache = str(candidate.resolve())
                    logger.info(f"【FFmpeg检测】找到本地FFmpeg: {_ffmpeg_path_cache}")
                    return _ffmpeg_path_cache
            current_dir = current_dir.parent
    except Exception: pass
    _ffmpeg_path_cache = ""
    logger.info("【FFmpeg检测】未找到FFmpeg，将使用imageio编码")
    return None

def get_http_session(proxy: str = "", verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    session.verify = verify_ssl
    if proxy.strip():
        session.proxies = {"http": proxy.strip(), "https": proxy.strip()}
        session.trust_env = False
    else:
        # 如果没有填代理，则使用系统网络环境（兼容 VPN 或直连）
        session.trust_env = True
    retry = Retry(total=2, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50, pool_block=True)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Connection": "keep-alive", "Keep-Alive": "timeout=300, max=200"})
    return session

class AgnesVideoWrapper:
    def __init__(self, frames: torch.Tensor, fps: float = 24.0, raw_video_bytes: bytes = b""):
        if frames.dtype == torch.uint8: frames = frames.float() / 255.0
        if frames.dim() == 4: self.frames = frames.unsqueeze(0)
        elif frames.dim() == 5: self.frames = frames
        else: raise ValueError(f"不支持的视频张量维度: {frames.dim()}")
        self._batch, self._t, self._h, self._w, self._c = self.frames.shape
        self.fps = float(fps)
        self._metadata: Dict[str, Any] = {}
        self._raw_bytes = raw_video_bytes

    def get_dimensions(self) -> Tuple[int, int]: return (self._w, self._h)
    def __len__(self) -> int: return self._t
    def __getitem__(self, idx): return self.frames[idx]
    @property
    def shape(self) -> torch.Size: return self.frames.shape
    @property
    def duration(self) -> float: return self._t / self.fps
    @property
    def metadata(self) -> Dict[str, Any]: return self._metadata
    @metadata.setter
    def metadata(self, value: Dict[str, Any]): self._metadata = value or {}
    def get_frame(self, index: int) -> torch.Tensor: return self.frames[0, index]

    def save_to(self, output_path, *, format="mp4", codec="auto", pix_fmt="yuv420p", audio_file=None, metadata=None, **kwargs):
        output_dir = os.path.dirname(output_path)
        if output_dir: os.makedirs(output_dir, exist_ok=True)
        if metadata: self._metadata.update(metadata)
        is_mp4_target = format in ("mp4", "video/mp4", "auto") or output_path.lower().endswith(".mp4")
        no_transcode_needed = (codec in ("auto", None)) and (audio_file is None) and is_mp4_target
        if len(self._raw_bytes) > 1024 and no_transcode_needed:
            try:
                with open(output_path, "wb") as f: f.write(self._raw_bytes)
                if os.path.getsize(output_path) == len(self._raw_bytes):
                    logger.info("【视频保存】原始视频无损直传完成")
                    return self._build_result_dict(output_path)
            except Exception as e: logger.warning(f"【视频保存】直传失败，降级编码: {str(e)}")
        frames_np = self.frames[0].clamp(0.0, 1.0).cpu().numpy()
        frames_np = (frames_np * 255).astype(np.uint8)
        if frames_np.shape[-1] == 4: frames_np = frames_np[..., :3]
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            try:
                self._encode_with_ffmpeg(ffmpeg_path, output_path, frames_np, format, codec, pix_fmt, audio_file)
                logger.info(f"【视频保存】FFmpeg编码完成: {os.path.basename(output_path)}")
                return self._build_result_dict(output_path)
            except Exception as e: logger.warning(f"【视频保存】FFmpeg失败，降级imageio: {str(e)}")
        try:
            writer_kwargs = {"fps": self.fps}
            if format and format != "auto": writer_kwargs["format"] = format
            if codec and codec != "auto": writer_kwargs["codec"] = codec
            writer = imageio.get_writer(output_path, **writer_kwargs)
            try:
                for frame in frames_np: writer.append_data(frame)
            finally: writer.close()
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                logger.info("【视频保存】imageio编码完成")
                return self._build_result_dict(output_path)
        except Exception as e: logger.warning(f"【视频保存】imageio失败，降级OpenCV: {str(e)}")
        try:
            self._encode_with_opencv(output_path, frames_np)
            logger.info("【视频保存】OpenCV兜底完成")
            return self._build_result_dict(output_path)
        except Exception as e:
            if len(self._raw_bytes) > 1024:
                logger.warning("【视频保存】所有编码均失败，强制输出原始视频")
                with open(output_path, "wb") as f: f.write(self._raw_bytes)
                return self._build_result_dict(output_path)
            raise RuntimeError(f"视频保存失败: {str(e)}")

    def _build_result_dict(self, output_path: str) -> Dict[str, str]:
        import folder_paths
        abs_path = os.path.abspath(output_path)
        base_output = os.path.abspath(folder_paths.get_output_directory())
        subfolder = ""
        try:
            rel = os.path.relpath(os.path.dirname(abs_path), base_output)
            if rel != ".": subfolder = rel
        except ValueError: pass
        return {"filename": os.path.basename(abs_path), "subfolder": subfolder, "type": "output"}

    def _encode_with_ffmpeg(self, ffmpeg_path, file_path, frames_np, fmt, codec, pix_fmt, audio_file):
        cmd = [ffmpeg_path, "-y", "-f", "rawvideo", "-vcodec", "rawvideo", "-s", f"{self._w}x{self._h}", "-pix_fmt", "rgb24", "-r", str(self.fps), "-i", "-", "-pix_fmt", pix_fmt, "-movflags", "+faststart"]
        if codec and codec != "auto": cmd.extend(["-vcodec", codec])
        else: cmd.extend(["-vcodec", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high"])
        if audio_file and os.path.isfile(audio_file): cmd.extend(["-i", audio_file, "-c:a", "aac", "-b:a", "320k", "-shortest"])
        cmd.append(file_path)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**8, creationflags=flags)
        try:
            for frame in frames_np: proc.stdin.write(frame.tobytes())
            proc.stdin.close()
            ret = proc.wait(timeout=600)
            if ret != 0:
                err = proc.stderr.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"FFmpeg错误: {err[-600:]}")
            if not os.path.exists(file_path) or os.path.getsize(file_path) < 1024: raise RuntimeError("输出文件异常")
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()

    def _encode_with_opencv(self, file_path, frames_np):
        fourcc_list = [("mp4v", ".mp4"), ("MJPG", ".avi"), ("XVID", ".avi")]
        last_err = None
        frame_count = len(frames_np)
        for code, ext in fourcc_list:
            cur_path = file_path
            if not cur_path.lower().endswith(ext):
                base, _ = os.path.splitext(cur_path); cur_path = base + ext
            fourcc = cv2.VideoWriter_fourcc(*code)
            writer = cv2.VideoWriter(cur_path, fourcc, self.fps, (self._w, self._h))
            if not writer.isOpened():
                last_err = f"编码器{code}初始化失败"; continue
            try:
                ok = 0
                for i in range(frame_count):
                    bgr = cv2.cvtColor(frames_np[i], cv2.COLOR_RGB2BGR)
                    if writer.write(bgr): ok += 1
                writer.release()
                if ok == frame_count and os.path.exists(cur_path) and os.path.getsize(cur_path) > 1024:
                    if cur_path != file_path:
                        try:
                            if os.path.exists(file_path): os.remove(file_path)
                            os.rename(cur_path, file_path)
                        except: pass
                    return
                else: last_err = f"编码器{code}写入{ok}/{frame_count}帧"
            except Exception as e:
                last_err = str(e)
                if writer.isOpened(): writer.release()
        raise RuntimeError(f"OpenCV全部编码器失败，最后错误: {last_err}")

CHUNK_SIZE = 8192
MAX_POLL_SECONDS = 7200
NETWORK_TIMEOUT = 300  
MAX_INT32 = 2 ** 31 - 1

# 官方公开接口常量
BASE_QUERY = "https://apihub.agnes-ai.com/agnesapi"
BASE_QUERY_OLD = "https://apihub.agnes-ai.com/v1/videos"
MODEL = "agnes-video-v2.0"
CHAT_ENDPOINT = "https://apihub.agnes-ai.com/v1/chat/completions"
FLASH_MODEL = "agnes-2.0-flash"
VISION_TIMEOUT = 120
VISION_RETRY = 3
VISION_TPL = """你是专业视频提示词工程师，根据图片生成JSON格式的正负向提示词：
{"positive":"正向提示词，电影级画质，细节丰富，描述精准","negative":"负面提示词，低质、模糊、畸形、水印等"}
只输出JSON，不要多余内容。"""
DEFAULT_NEG = "模糊,低分辨率,畸形,水印,文字,多余肢体,扭曲,崩坏,低画质,闪烁,卡顿,色彩失真,伪影,噪点"
FALLBACK_POS = "电影级动态视频，高清画质，平滑运镜，自然光影，丰富细节，流畅动画，真实质感"
FALLBACK_NEG = DEFAULT_NEG
CACHE_DIR = Path("./agnes_task_cache")
CACHE_DIR.mkdir(exist_ok=True)

# ====================== 【12 图床集群】免登录容灾上传器 ======================
class MultiImageUploader:
    CACHE_FILE = Path("./agnes_image_cache.json")
    
    SUPPORTED_HOSTS = [
        "freeimagehost",
        "postimages",   
        "pixeldrain",   
        "uploadcc",      
        "vgy",           
        "telegraph",     
        "jpgfi",         
        "uguu",          
        "pomfcat",       
        "imgr",          
        "imagefile",     
        "b2pics"         
    ]

    @staticmethod
    def load_cache() -> Dict[str, str]:
        if MultiImageUploader.CACHE_FILE.exists():
            try:
                with open(MultiImageUploader.CACHE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    @staticmethod
    def save_cache(cache: Dict[str, str]):
        try:
            with open(MultiImageUploader.CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
        except:
            pass

    @staticmethod
    def _upload_to_host(file_path: str, host: str, proxy: str, freeimage_key: str = "", verify_ssl: bool = True) -> Optional[str]:
        filename = os.path.basename(file_path)
        session = requests.Session()
        session.verify = verify_ssl
        if proxy.strip():
            session.proxies = {"http": proxy, "https": proxy}
        session.headers.update({"Connection": "keep-alive", "User-Agent": "Mozilla/5.0"})
        
        try:
            with open(file_path, "rb") as f:
                if host == "freeimagehost":
                    # 未配置密钥时自动跳过
                    if not freeimage_key.strip():
                        logger.debug(f"【图床集群】freeimagehost 未配置API密钥，跳过")
                        return None
                    url = "https://freeimage.host/api/1/upload"
                    files = {"source": (filename, f)}
                    data = {"key": freeimage_key.strip(), "action": "upload", "format": "json"}
                    resp = session.post(url, data=data, files=files, timeout=30)
                    resp.raise_for_status()
                    res_json = resp.json()
                    if res_json.get("status_code") == 200:
                        return res_json.get("image", {}).get("url")
                    raise RuntimeError(f"Freeimage 拒绝: {res_json.get('status_txt', '未知')}")

                elif host == "postimages":
                    url = "https://postimages.org/api/upload"
                    files = {"upload": (filename, f)}
                    data = {"uploadtype": "file", "format": "json"}
                    resp = session.post(url, data=data, files=files, timeout=30)
                    resp.raise_for_status()
                    res_json = resp.json()
                    if res_json.get("success"):
                        return res_json.get("url")
                    raise RuntimeError("Postimages 上传失败")

                elif host == "pixeldrain":
                    url = "https://pixeldrain.com/api/file"
                    files = {"file": (filename, f)}
                    resp = session.post(url, files=files, timeout=30)
                    resp.raise_for_status()
                    res_json = resp.json()
                    if res_json.get("success"):
                        return f"https://pixeldrain.com/u/{res_json.get('id')}"
                    raise RuntimeError("Pixeldrain 上传失败")

                elif host == "uploadcc":
                    url = "https://upload.cc/upload"
                    files = {"file": (filename, f)}
                    data = {"format": "json"}
                    resp = session.post(url, data=data, files=files, timeout=30)
                    resp.raise_for_status()
                    res_json = resp.json()
                    if res_json.get("code") == 200:
                        return res_json["data"]["url"]
                    raise RuntimeError(f"Upload.cc 拒绝: {res_json.get('msg', '未知')}")

                elif host == "vgy":
                    url = "https://vgy.me/upload"
                    files = {"file": (filename, f)}
                    resp = session.post(url, files=files, timeout=30)
                    resp.raise_for_status()
                    res_json = resp.json()
                    if res_json.get("url"):
                        return res_json["url"]
                    raise RuntimeError("Vgy.me 上传失败")

                elif host == "telegraph":
                    url = "https://telegra.ph/upload"
                    files = {"file": (filename, f)}
                    resp = session.post(url, files=files, timeout=30)
                    resp.raise_for_status()
                    res_json = resp.json()
                    if len(res_json) > 0 and "src" in res_json[0]:
                        return "https://telegra.ph" + res_json[0]["src"]
                    raise RuntimeError("Telegraph 上传失败")

                elif host == "jpgfi":
                    url = "https://jpg.fi/upload"
                    files = {"file": (filename, f)}
                    resp = session.post(url, files=files, timeout=30)
                    resp.raise_for_status()
                    text = resp.text.strip()
                    if text.startswith("https://"):
                        return text
                    raise RuntimeError(f"JPG.FI 返回异常: {text}")

                elif host == "uguu":
                    url = "https://uguu.se/api.php?d=upload-tool"
                    files = {"file": (filename, f)}
                    resp = session.post(url, files=files, timeout=30)
                    resp.raise_for_status()
                    text = resp.text.strip()
                    if text.startswith("https://"):
                        return text
                    raise RuntimeError(f"Uguu 返回异常: {text}")

                elif host == "pomfcat":
                    url = "https://pomf.lain.la/upload"
                    files = {"files[]": (filename, f)}
                    resp = session.post(url, files=files, timeout=30)
                    resp.raise_for_status()
                    res_json = resp.json()
                    if res_json.get("success"):
                        return res_json.get("files")[0]["url"]
                    raise RuntimeError("Pomf 上传失败")

                elif host == "imgr":
                    url = "https://imgr.xyz/upload"
                    files = {"file": (filename, f)}
                    resp = session.post(url, files=files, timeout=30)
                    resp.raise_for_status()
                    text = resp.text.strip()
                    if text.startswith("http"):
                        return text
                    raise RuntimeError(f"ImgR 返回异常: {text}")

                elif host == "imagefile":
                    url = "https://imagefile.ru/api/v1/upload"
                    files = {"file": (filename, f)}
                    resp = session.post(url, files=files, timeout=30)
                    resp.raise_for_status()
                    res_json = resp.json()
                    if res_json.get("status") == 200:
                        return res_json.get("data", {}).get("link")
                    raise RuntimeError(f"ImageFile 拒绝: {res_json.get('message', '未知')}")

                elif host == "b2pics":
                    url = "https://b2.pics/upload"
                    files = {"file": (filename, f)}
                    resp = session.post(url, files=files, timeout=30)
                    resp.raise_for_status()
                    text = resp.text.strip()
                    if text.startswith("https://"):
                        return text
                    raise RuntimeError(f"B2.Pics 返回异常: {text}")

        except Exception as e:
            logger.warning(f"【图床集群】{host} 尝试失败: {str(e)}")
            return None
        finally:
            session.close()
        return None

    @staticmethod
    def upload_file(file_path: str, proxy: str = "", freeimage_key: str = "", verify_ssl: bool = True) -> str:
        cache = MultiImageUploader.load_cache()
        abs_path = str(Path(file_path).resolve())
        if abs_path in cache:
            logger.info(f"【图床集群】命中缓存: {os.path.basename(file_path)}")
            return cache[abs_path]

        if not os.path.exists(file_path):
            raise RuntimeError(f"文件不存在: {file_path}")
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise RuntimeError(f"文件为空: {file_path}")
        if file_size > 200 * 1024 * 1024:
            raise RuntimeError(f"文件超过200MB上限: {file_path}")

        logger.info(f"【图床集群】开始上传: {os.path.basename(file_path)}")
        
        failed_hosts = []
        for host in MultiImageUploader.SUPPORTED_HOSTS:
            url = MultiImageUploader._upload_to_host(file_path, host, proxy, freeimage_key, verify_ssl)
            if url:
                logger.info(f"【图床集群】✅ {host} 上传成功: {url}")
                cache[abs_path] = url
                MultiImageUploader.save_cache(cache)
                return url
            else:
                failed_hosts.append(host)

        raise RuntimeError(
            f"【图床集群】12个图床全部尝试失败！\n"
            f"失败列表: {', '.join(failed_hosts)}\n"
            f"检查项：网络环境是否正常？是否需要配置代理？"
        )

    @staticmethod
    def process_folder_images(folder_path: str, proxy: str = "", freeimage_key: str = "", verify_ssl: bool = True) -> List[str]:
        folder = Path(folder_path)
        if not folder.exists() or not folder.is_dir():
            raise RuntimeError(f"文件夹不存在: {folder_path}")

        image_files = []
        for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif"]:
            image_files.extend(folder.glob(ext))
            
        if not image_files:
            raise RuntimeError(f"文件夹中未找到图片: {folder_path}")

        def get_number(p):
            nums = re.findall(r'\d+', p.stem)
            return int(nums[0]) if nums else 999999
        image_files.sort(key=get_number)
        
        logger.info(f"【文件夹模式】找到 {len(image_files)} 张图片，按序上传")
        urls = []
        for f in image_files:
            urls.append(MultiImageUploader.upload_file(str(f), proxy, freeimage_key, verify_ssl))
        return urls

def save_cache(task_id: str, data: Dict):
    try:
        with open(CACHE_DIR / f"{task_id}.pkl", 'wb') as f:
            pickle.dump({"task_id": task_id, "data": data, "time": time.time()}, f)
    except: pass

def load_cache(task_id: str) -> Optional[Dict]:
    p = CACHE_DIR / f"{task_id}.pkl"
    if not p.exists(): return None
    try:
        with open(p, 'rb') as f: return pickle.load(f)
    except: return None

class PromptHelper:
    @staticmethod
    def vision_gen(api_key: str, imgs: List[str], proxy: str = "", verify_ssl: bool = True) -> Dict[str, str]:
        tag = "【识图生成提示词】"
        sess = get_http_session(proxy, verify_ssl)
        headers = {"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}
        content = [{"type": "text", "text": VISION_TPL.strip()}]
        for url in imgs:
            content.append({"type": "image_url", "image_url": {"url": url.strip()}})
        payload = {"model": FLASH_MODEL, "messages": [{"role": "user", "content": content}], "temperature": 0.4, "max_tokens": 2048}
        for i in range(VISION_RETRY):
            try:
                logger.info(f"{tag} 第{i+1}次调用，图片数：{len(imgs)}")
                r = sess.post(CHAT_ENDPOINT, headers=headers, json=payload, timeout=(NETWORK_TIMEOUT, VISION_TIMEOUT))
                if r.status_code in (401, 403): break
                r.raise_for_status()
                res = r.json()
                text = res["choices"][0]["message"]["content"].strip()
                s, e = text.find("{"), text.rfind("}") + 1
                if s == -1 or e == 0: raise ValueError("返回非JSON")
                data = json.loads(text[s:e])
                pos = data.get("positive", "").strip()
                neg = data.get("negative", "").strip()
                if not pos: raise ValueError("正向提示词为空")
                logger.info(f"{tag} 成功 | 正向前80字：{pos[:80]}...")
                sess.close()
                return {"positive": pos, "negative": neg}
            except Exception as e:
                wait = 2 * (i + 1)
                logger.warning(f"{tag} 第{i+1}次失败：{str(e)[:80]}，等待{wait}秒")
                time.sleep(wait)
        sess.close()
        logger.warning(f"{tag} 全部失败，使用通用降级提示词")
        return {"positive": FALLBACK_POS, "negative": FALLBACK_NEG}

def decode_video(vid_bytes: bytes, tw: int, th: int, t_frames: int) -> torch.Tensor:
    frames = []
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(vid_bytes); f.flush(); tmp = f.name
        cap = cv2.VideoCapture(tmp)
        if cap.isOpened():
            while cap.isOpened() and len(frames) < t_frames * 2:
                ret, frame = cap.read()
                if not ret: break
                if frame.shape[:2] != (th, tw): frame = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_LANCZOS4)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames.append(np.array(rgb, dtype=np.float32) / 255.0)
            cap.release()
        logger.info(f"【视频解码】读取到{len(frames)}帧，分辨率{tw}x{th}")
    except Exception as e:
        logger.warning(f"【视频解码】OpenCV失败: {e}")
        try:
            from io import BytesIO
            stream = BytesIO(vid_bytes)
            reader = imageio.get_reader(stream, format="mp4")
            frames.clear()
            for frame in reader:
                if frame.shape[:2] != (th, tw): frame = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_LANCZOS4)
                frames.append(np.array(frame, dtype=np.float32) / 255.0)
            reader.close()
            logger.info(f"【视频解码】imageio兜底读取{len(frames)}帧")
        except Exception as e2: logger.error(f"【视频解码】imageio也失败: {e2}")
    finally:
        if tmp and os.path.exists(tmp):
            try: os.unlink(tmp)
            except: pass
    if not frames: raise RuntimeError("视频解码失败，文件可能损坏")
    if len(frames) > t_frames:
        step = len(frames) / t_frames
        frames = [frames[int(i * step)] for i in range(t_frames)]
    elif len(frames) < t_frames:
        frames += [frames[-1]] * (t_frames - len(frames))
    return torch.from_numpy(np.stack(frames, axis=0)).unsqueeze(0)

class XiaoMoAgnesVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pos_text": ("STRING", {"default": "", "forceInput": True}),
                "neg_text": ("STRING", {"default": "", "forceInput": True}),
                "api_key": ("STRING", {"multiline": False, "placeholder": "Agnes API密钥"}),
                "gen_mode": (["auto", "txt2vid", "img2vid", "multi_img"], {"default": "auto"}),
                "music_platform": (["none"], {"default": "none"}),
                "music_api_key": ("STRING", {"multiline": False, "default": ""}),
                "img_url": ("STRING", {"multiline": True, "default": ""}),
                "width": ("INT", {"default": 1152, "min": 512, "max": 2048, "step": 8}),
                "height": ("INT", {"default": 768, "min": 512, "max": 2048, "step": 8}),
                "num_frames": ("INT", {"default": 81, "min": 81, "max": 441, "step": 8}),
                "frame_rate": ("INT", {"default": 24, "min": 1, "max": 60}),
                "num_inference_steps": ("INT", {"default": 0, "min": 0, "max": 100}),
                "seed": ("INT", {"default": 123456}),
                "seed_mode": (["randomize", "fixed"], {"default": "randomize"}),
                "poll_interval": ("INT", {"default": 15, "min": 5, "max": 30}),
                "http_proxy": ("STRING", {"default": ""}),
                "task_recovery_id": ("STRING", {"default": ""}),
            },
            "optional": {
                "api_create_endpoint": ("STRING", {"default": "https://apihub.agnes-ai.com/agnesapi", "multiline": False}),
                "freeimage_api_key": ("STRING", {"default": "", "multiline": False, "placeholder": "Freeimage图床密钥，可选"}),
                "disable_ssl_verify": ("BOOLEAN", {"default": False}),
            }
        }
    RETURN_TYPES = ("VIDEO", "STRING", "BOOLEAN", "STRING")
    RETURN_NAMES = ("视频", "完整任务JSON", "是否需要音频", "任务ID")
    FUNCTION = "run"
    CATEGORY = "肖默定制/Agnes视频生成"

    def run(self, pos_text, neg_text, api_key, gen_mode, music_platform, music_api_key,
            img_url, width, height, num_frames, frame_rate, num_inference_steps,
            seed, seed_mode, poll_interval, http_proxy, task_recovery_id,
            api_create_endpoint = "https://apihub.agnes-ai.com/agnesapi",
            freeimage_api_key = "",
            disable_ssl_verify = False):
        
        # 动态计算SSL验证开关
        verify_ssl = not disable_ssl_verify
        
        current_input_hash = hash((pos_text.strip(), neg_text.strip(), img_url.strip(), width, height, num_frames, frame_rate, seed, gen_mode))
        if hasattr(self, '_last_input_hash') and self._last_input_hash == current_input_hash:
            tag = "【Agnes视频主流程】"
            logger.info(f"{tag} 检测到输入参数未变化，本次跳过（避免重复提交/扣费）")
            if hasattr(self, '_last_result'): return self._last_result
            return (None, "{}", False, "")
        self._last_input_hash = current_input_hash

        try:
            import inspect
            for frame_info in inspect.stack():
                frame = frame_info.frame
                if 'unique_id' in frame.f_locals:
                    set_current_node_id(frame.f_locals['unique_id']); break
        except Exception: pass

        tag = "【Agnes视频主流程】"
        logger.info("=" * 70)
        logger.info(f"{tag} 任务启动")
        update_frontend_progress(5)
        key = api_key.strip()
        if not key: raise RuntimeError("Agnes API密钥不能为空")
        if (num_frames - 1) % 8 != 0: raise RuntimeError("num_frames必须满足 8n+1 规则")
        
        # ==================== 统一入口解析（URL / 文件夹 / 文件） ====================
        img_url_input = img_url.strip()
        imgs = []
        if img_url_input:
            if img_url_input.startswith(('http://', 'https://')):
                imgs = [u.strip() for u in img_url_input.split(",") if u.strip()]
                logger.info(f"【URL模式】检测到 {len(imgs)} 个公网链接")
            elif os.path.isdir(img_url_input):
                # 本地文件夹模式
                logger.info(f"【文件夹模式】检测到本地路径: {img_url_input}")
                try:
                    imgs = MultiImageUploader.process_folder_images(img_url_input, http_proxy, freeimage_api_key, verify_ssl)
                except Exception as e:
                    logger.error(f"【文件夹模式】处理失败: {e}")
                    raise
            elif os.path.isfile(img_url_input):
                # 单张本地图片文件
                logger.info(f"【单文件模式】检测到本地图片: {img_url_input}")
                imgs.append(MultiImageUploader.upload_file(img_url_input, http_proxy, freeimage_api_key, verify_ssl))
            else:
                raise RuntimeError(f"img_url 无效，无法识别为有效的公网链接或本地文件路径: {img_url_input}")
        # =====================================================================
        
        has_img = len(imgs) > 0
        
        if gen_mode == "auto":
            mode = "txt2vid" if not has_img else "img2vid" if len(imgs) == 1 else "multi_img"
        else: mode = gen_mode
        
        if mode in ("multi_img", "img2vid") and len(imgs) > 1:
            logger.info("【关键帧模式】检测到多张图片，将按官方文档 `keyframes` 格式提交参数")
            mode = "multi_img"
        if mode in ("img2vid", "multi_img") and not has_img:
            raise RuntimeError(f"{mode}模式必须传入图片URL或指定图片文件夹")

        final_pos = pos_text.strip()
        final_neg = neg_text.strip()
        if not final_pos and not has_img: raise RuntimeError("文生视频必须填写正向提示词")
        if not final_pos and has_img:
            update_frontend_progress(8)
            res = PromptHelper.vision_gen(key, imgs, http_proxy, verify_ssl)
            final_pos = res["positive"]
            final_neg = (final_neg + "，" + res["negative"]) if final_neg else res["negative"]
        if not final_neg: final_neg = DEFAULT_NEG

        logger.info(f"{tag} 最终正向提示词: {final_pos[:120]}...")
        sess = get_http_session(http_proxy, verify_ssl)
        vid_id = task_recovery_id.strip()
        vid_bytes = b""
        tensor = None
        aw, ah = width, height
        last_info = None

        if vid_id:
            cached_task = load_cache(vid_id)
            if cached_task:
                task_data = cached_task["data"]
                logger.info(f"{tag} 检测到任务恢复，已加载本地缓存 | 模式: {task_data.get('mode', '未知')}")
                mode = task_data.get("mode", mode)
                final_pos = task_data.get("prompt", final_pos)
                final_neg = task_data.get("negative_prompt", final_neg or DEFAULT_NEG)
                recovered_w = task_data.get("width")
                recovered_h = task_data.get("height")
                if recovered_w and recovered_h: width, height = recovered_w, recovered_h; aw, ah = width, height
                recovered_frames = task_data.get("num_frames")
                recovered_rate = task_data.get("frame_rate")
                if recovered_frames: num_frames = recovered_frames
                if recovered_rate: frame_rate = recovered_rate
                recovered_steps = task_data.get("num_inference_steps")
                if recovered_steps is not None: num_inference_steps = recovered_steps
                recovered_seed = task_data.get("seed")
                if recovered_seed is not None: seed = recovered_seed

        try:
            if not vid_id:
                payload = {
                    "model": MODEL, "prompt": final_pos, "negative_prompt": final_neg,
                    "width": width, "height": height, "num_frames": num_frames, "frame_rate": frame_rate,
                }
                if num_inference_steps > 0: payload["num_inference_steps"] = num_inference_steps
                payload["seed"] = seed if seed_mode == "fixed" else np.random.randint(0, MAX_INT32)

                if mode in ("img2vid", "multi_img") and imgs:
                    if len(imgs) == 1: payload["image"] = imgs[0]
                    else:
                        payload.setdefault("extra_body", {})["image"] = imgs
                        payload.setdefault("extra_body", {})["mode"] = "keyframes"
                
                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                r = sess.post(api_create_endpoint, headers=headers, json=payload, timeout=NETWORK_TIMEOUT)
                
                if r.status_code != 200:
                    error_msg = r.text
                    logger.error(f"【💥 错误详情】Agnes API 拒绝了你的请求！状态码：{r.status_code}")
                    logger.error(f"【官方返回的具体原因】{error_msg}")
                    raise RuntimeError(f"API 请求失败 (HTTP {r.status_code})，请查看上方日志获取详细错误信息。")
                
                data = r.json()
                vid_id = data.get("video_id") or data.get("id") or data.get("task_id")
                if not vid_id: raise RuntimeError("未返回有效任务ID")
                logger.info(f"{tag} 任务提交成功 | ID: {vid_id}")
                save_cache(vid_id, {
                    "prompt": final_pos, "negative_prompt": final_neg, "seed": payload.get("seed"),
                    "mode": mode, "width": width, "height": height, "num_frames": num_frames,
                    "frame_rate": frame_rate, "num_inference_steps": num_inference_steps
                })

            waited = 0
            video_url = None
            while waited < MAX_POLL_SECONDS:
                time.sleep(poll_interval)
                waited += poll_interval
                info = None
                try:
                    params = {"video_id": vid_id, "model_name": MODEL}
                    r = sess.get(BASE_QUERY, headers={"Authorization": f"Bearer {key}"}, params=params, timeout=30)
                    r.raise_for_status()
                    info = r.json()
                except Exception:
                    try:
                        r = sess.get(f"{BASE_QUERY_OLD}/{vid_id}", headers={"Authorization": f"Bearer {key}"}, timeout=30)
                        r.raise_for_status()
                        info = r.json()
                    except Exception as query_err:
                        logger.warning(f"{tag} 轮询查询失败，下一轮自动重试: {str(query_err)[:80]}")
                if not info: continue
                if "size" in info and info["size"]:
                    size_str = info["size"]
                    if "x" in size_str:
                        try:
                            w_str, h_str = size_str.lower().split("x")
                            new_w, new_h = int(w_str), int(h_str)
                            if (new_w, new_h) != (aw, ah): aw, ah = new_w, new_h
                        except: pass
                last_info = info
                status = info.get("status", "")
                prog = int(info.get("progress", 0))
                update_frontend_progress(10 + prog * 0.85)
                if status == "completed":
                    video_url = info.get("remixed_from_video_id") or info.get("url") or info.get("video_url")
                    if video_url:
                        logger.info(f"{tag} 🎉 生成完成！视频URL已获取")
                        break
                elif status == "failed":
                    raise RuntimeError(f"生成失败: {info.get('error', '未知错误')}")
                remaining = MAX_POLL_SECONDS - waited
                if 0 < remaining <= 60 and int(remaining) % 30 == 0:
                    logger.warning(f"{tag} 任务即将超时，剩余约 {int(remaining)} 秒，当前进度 {prog}%")
            if not video_url:
                last_status = last_info.get("status", "未知") if last_info else "查询无响应"
                last_prog = last_info.get("progress", 0) if last_info else 0
                raise RuntimeError(f"轮询超时（已等待{waited}秒），最后状态: {last_status}，进度: {last_prog}%")

            update_frontend_progress(93)
            dl = sess.get(video_url, stream=True, timeout=180)
            dl.raise_for_status()
            vid_bytes = b"".join(dl.iter_content(CHUNK_SIZE))
            update_frontend_progress(96)
            tensor = decode_video(vid_bytes, aw, ah, num_frames)

        except Exception as e:
            if hasattr(self, '_last_input_hash'): delattr(self, '_last_input_hash')
            logger.exception(f"{tag} 执行异常")
            raise
        finally:
            try:
                if 'sess' in locals() and sess is not None: sess.close()
            except: pass
            try: import gc; gc.collect()
            except: pass
            update_frontend_progress(100)

        video_out = AgnesVideoWrapper(frames=tensor, fps=float(frame_rate), raw_video_bytes=vid_bytes)
        out_json = json.dumps({
            "mode": mode, "duration": round(num_frames / frame_rate, 2),
            "resolution": f"{aw}x{ah}", "task_id": vid_id
        }, ensure_ascii=False, indent=2)
        self._last_result = (video_out, out_json, False, vid_id)
        logger.info(f"{tag} ✅ 节点执行完成")
        return self._last_result

NODE_CLASS_MAPPINGS = {"XiaoMoAgnesVideo": XiaoMoAgnesVideo}
NODE_DISPLAY_NAME_MAPPINGS = {"XiaoMoAgnesVideo": "XiaoMo Agnes Video V2.0"}
import requests
import ssl
import urllib3
requests.adapters.DEFAULT_RETRIES = 8
import time
import json
import tempfile
import os
import torch
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager
import cv2
from io import BytesIO

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
requests.packages.urllib3.disable_warnings()

# -------------------------- API 常量（严格对齐官方文档） --------------------------
BASE_CREATE_URL = "https://apih.agnes-ai.com/v1/videos"
BASE_VIDEO_QUERY_URL = "https://apih.agnes-ai.com/agnesapi"
MODEL_NAME = "agnes-video-v2.0"

# -------------------------- 【新增：SSL兼容适配器，仅此处修改解决EOF报错】 --------------------------
class SSLFixAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # 跨境网络专用：强制仅TLS1.2，关闭TLS1.3避免握手断开
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        # 降低加密安全等级，兼容海外老旧服务器链路
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        ctx.options |= ssl.OP_NO_COMPRESSION
        ctx.options |= ssl.OP_NO_TICKET
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
            **kwargs
        )

# -------------------------- 请求会话（替换为SSL加固版本，业务逻辑不变） --------------------------
def get_http_session():
    session = requests.Session()
    session.trust_env = False
    retry = Retry(
        total=8,
        backoff_factor=3,
        status_forcelist=[429, 500, 502, 503, 504, 408],
        allowed_methods=["GET", "POST"]
    )
    # 使用修复SSL的适配器
    adapter = SSLFixAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.timeout = (60, 300)
    return session

# -------------------------- 标准视频包装类（修复浏览器预览）【完全原样，无修改】 --------------------------
class ComfyVideoWrapper:
    def __init__(self, tensor):
        self.tensor = tensor
        # ComfyUI 标准视频张量格式: [B, T, H, W, C]
        self.batch, self.frames, self.height, self.width, self.channels = tensor.shape
        self.fps = 24
        self.duration = self.frames / self.fps

    def get_dimensions(self):
        return (self.width, self.height)

    def save_to(self, filepath, frame_rate=24, format="mp4", codec="libx264", **kwargs):
        import imageio

        # 强制浏览器兼容编码标准
        final_format = "mp4"
        final_codec = "libx264"
        if format and isinstance(format, str) and format.strip() and format.lower() not in ("auto", "none"):
            final_format = format.strip().lower()
        if codec and isinstance(codec, str) and codec.strip() and codec.lower() not in ("auto", "none"):
            final_codec = codec.strip().lower()

        fps = frame_rate if frame_rate and frame_rate > 0 else 24
        self.fps = fps
        self.duration = self.frames / fps

        # 帧数据标准化：强制 3通道 RGB uint8
        frames_np = self.tensor.squeeze(0).clamp(0.0, 1.0).cpu().numpy()
        frames_np = (frames_np * 255).astype(np.uint8)
        
        if frames_np.shape[-1] == 4:
            frames_np = frames_np[..., :3]
        elif frames_np.shape[-1] == 1:
            frames_np = np.repeat(frames_np, 3, axis=-1)
        elif frames_np.shape[-1] != 3:
            raise ValueError(f"不支持的通道数: {frames_np.shape[-1]}，仅支持3通道RGB")

        # 浏览器兼容编码参数（核心修复预览黑屏）
        ffmpeg_params = [
            "-pix_fmt", "yuv420p",
            "-profile:v", "baseline",
            "-level", "3.0",
            "-g", str(fps),
            "-movflags", "+faststart"
        ]

        try:
            writer = imageio.get_writer(
                filepath,
                fps=fps,
                format=final_format,
                codec=final_codec,
                output_params=ffmpeg_params,
                macro_block_size=8
            )
            for frame in frames_np:
                writer.append_data(frame)
            writer.close()
        except Exception as e:
            raise RuntimeError(f"视频编码失败: {str(e)}") from e

        if not os.path.exists(filepath) or os.path.getsize(filepath) < 1024:
            raise RuntimeError("视频生成失败，文件异常")

    def __getitem__(self, idx):
        return self.tensor[idx]

    @property
    def shape(self):
        return self.tensor.shape

# -------------------------- 节点主体【完整原样，输入、界面、参数一丝未改】 --------------------------
class XiaoMoAgnesVideo:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "api_key": ("STRING", {
                    "multiline": False,
                    "placeholder": "填写 sk- 开头的 Agnes API 密钥",
                    "default": ""
                }),
                "img_url": ("STRING", {
                    "multiline": True,
                    "placeholder": "图生视频填写公网图片直链，文生视频留空",
                    "default": ""
                }),
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "【正向提示词】填写主体+动作+场景+运镜+画质要求。可直接编辑，也可接入外部字符串节点"
                }),
                "negative_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "【反向提示词】填写需要规避的负面缺陷、崩坏效果。可直接编辑，也可接入外部字符串节点"
                }),
                "width": ("INT", {
                    "default": 720,
                    "min": 512,
                    "max": 2048,
                    "step": 64
                }),
                "height": ("INT", {
                    "default": 1280,
                    "min": 512,
                    "max": 2048,
                    "step": 64
                }),
                "num_frames": ("INT", {
                    "default": 81,
                    "min": 81,
                    "max": 441,
                    "step": 8
                }),
                "frame_rate": ("INT", {
                    "default": 24,
                    "min": 1,
                    "max": 60
                }),
                "seed": ("INT", {
                    "default": 123456,
                    "min": 0,
                    "max": 99999999
                }),
                "seed_mode": (["randomize", "fixed"], {
                    "default": "randomize"
                }),
                "poll_interval": ("INT", {
                    "default": 5,
                    "min": 2,
                    "max": 30
                }),
            }
        }

    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("视频输出", "完整任务JSON")
    FUNCTION = "run"
    CATEGORY = "肖默定制插件"

    def run(self, api_key, img_url, prompt, negative_prompt,
            width, height, num_frames, frame_rate, seed, seed_mode, poll_interval):

        # 自动修正帧数为官方 8n+1 规范
        if (num_frames - 1) % 8 != 0:
            num_frames = ((num_frames - 1) // 8) * 8 + 1
            num_frames = max(81, num_frames)
            print(f"⚠️ num_frames 自动修正为 {num_frames}（官方要求满足 8n+1）")

        if not api_key.strip():
            raise ValueError("请填写有效的 API Key")

        if not prompt.strip():
            raise ValueError("请填写正向提示词内容")

        final_prompt = prompt.strip()
        final_neg = negative_prompt.strip()

        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json"
        }
        session = get_http_session()

        # 严格对齐官方请求参数
        payload = {
            "model": MODEL_NAME,
            "prompt": final_prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "frame_rate": frame_rate,
        }

        img_clean = img_url.strip()
        if img_clean:
            payload["image"] = img_clean
        if final_neg:
            payload["negative_prompt"] = final_neg
        if seed_mode == "fixed":
            payload["seed"] = seed

        full_task_json = {}
        video_id = None
        video_url = None

        def cut_text(text, limit=80):
            return text[:limit] + "..." if len(text) > limit else text

        # ========== 1. 创建任务 ==========
        print("🚀【步骤1/4】提交 Agnes V2.0 视频生成任务...")
        print(f"📝 正向提示词：{cut_text(final_prompt)}")
        if final_neg:
            print(f"🚫 反向提示词：{cut_text(final_neg)}")
        if img_clean:
            print(f"🖼️ 参考图链接：{cut_text(img_clean)}")
        print(f"📐 分辨率：{width}×{height} | 帧数：{num_frames} | 帧率：{frame_rate}fps")

        try:
            resp = session.post(BASE_CREATE_URL, headers=headers, json=payload, timeout=(30, 300), verify=False)
            resp.raise_for_status()
            task_data = resp.json()
            full_task_json = task_data

            if "error" in task_data and task_data["error"]:
                raise Exception(f"API 返回错误: {task_data['error']}")

            video_id = task_data.get("video_id")
            if not video_id:
                raise Exception("API 未返回任务 ID")
            print(f"✅ 任务创建成功，任务ID: {video_id}")
        except Exception as e:
            raise RuntimeError(f"【创建任务失败】{str(e)}") from e

        # ========== 2. 轮询进度 ==========
        print("\n⏳【步骤2/4】轮询生成进度，最长等待 15 分钟...")
        waited = 0
        max_wait = 900
        while waited < max_wait:
            time.sleep(poll_interval)
            waited += poll_interval
            try:
                query_params = {"video_id": video_id, "model_name": MODEL_NAME}
                stat_resp = session.get(BASE_VIDEO_QUERY_URL, headers=headers, params=query_params, timeout=(30, 300), verify=False)
                stat_resp.raise_for_status()
                task_info = stat_resp.json()
                full_task_json = task_info

                if "error" in task_info and task_info["error"]:
                    raise Exception(f"生成失败: {task_info['error']}")

                status = task_info.get("status", "")
                progress = task_info.get("progress", 0)
                print(f"⏱️ 状态：{status} | 进度：{progress}% | 已等待 {waited}s")

                if status == "completed" or status == "success":
                    video_url = task_info.get("remixed_from_video_id") or task_info.get("video_url") or task_info.get("url")
                    if not video_url:
                        raise Exception("任务已完成，但未返回视频下载链接")
                    print("🎉 生成完成，开始下载视频...")
                    break
                elif status in ("failed", "error"):
                    raise Exception(f"生成失败: {task_info.get('error_msg', '未知原因')}")
            except requests.exceptions.RequestException:
                print(f"⚠️ 网络波动，{poll_interval} 秒后自动重试...")
                continue

        if not video_url:
            raise RuntimeError("任务超时，未在 15 分钟内完成生成")

        # ========== 3. 下载视频 ==========
        print("\n📥【步骤3/4】下载视频文件...")
        try:
            vid_resp = session.get(video_url, timeout=(30, 300), verify=False)
            vid_resp.raise_for_status()
            vid_bytes = vid_resp.content
            print(f"✅ 下载完成，文件大小：{round(len(vid_bytes)/1024/1024, 2)} MB")
        except Exception as e:
            raise RuntimeError(f"【下载视频失败】{str(e)}") from e

        # ========== 4. 解码转标准张量 ==========
        print("\n🔧【步骤4/4】解析视频帧并转换为 ComfyUI 标准张量...")
        frames = []
        temp_file_path = ""
        decode_success = False

        # 方案1：OpenCV 解码（速度快）
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(vid_bytes)
                temp_file_path = tmp.name
            cap = cv2.VideoCapture(temp_file_path)
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames.append(frame_rgb.astype(np.float32) / 255.0)
            cap.release()
            if frames:
                decode_success = True
        except Exception:
            print("⚠️ OpenCV 解码失败，切换 imageio 方案")
        finally:
            if os.path.exists(temp_file_path):
                try: os.unlink(temp_file_path)
                except: pass

        # 方案2：imageio 内存解码（兼容性好）
        if not decode_success:
            try:
                import imageio
                reader = imageio.get_reader(BytesIO(vid_bytes), format="mp4")
                for frame in reader:
                    frames.append(frame.astype(np.float32) / 255.0)
                reader.close()
                decode_success = True
            except Exception as e:
                raise RuntimeError(f"视频解码全部失败: {str(e)}") from e

        if not frames:
            raise RuntimeError("未读取到有效视频帧")

        # 转换为 ComfyUI 标准格式 [B, T, H, W, C]
        raw_tensor = torch.from_numpy(np.stack(frames, axis=0)).unsqueeze(0)
        video_out = ComfyVideoWrapper(raw_tensor)
        print(f"✅ 处理完成！共 {len(frames)} 帧，分辨率 {video_out.width}×{video_out.height}")

        try:
            output_json = json.dumps(full_task_json, ensure_ascii=False, indent=2)
        except:
            output_json = json.dumps({"info": "任务数据序列化失败"}, ensure_ascii=False, indent=2)

        return (video_out, output_json)

# 节点注册【原样不变】
NODE_CLASS_MAPPINGS = {
    "XiaoMoAgnesVideo": XiaoMoAgnesVideo
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "XiaoMoAgnesVideo": "肖默 - Agnes V2.0 视频生成"
}
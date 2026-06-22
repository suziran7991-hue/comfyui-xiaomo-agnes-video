import requests
import time
import json
from io import BytesIO
import torch
import numpy as np
import ffmpeg

# Agnes V2.0 图生视频节点
BASE_URL = "https://apihub.agnes-ai.com/v1"

class XiaoMoAgnesVideo:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "api_key": ("STRING", {"multiline": False, "placeholder": "sk-粘贴你的Agnes API密钥", "localized_name": "API密钥"}),
                "img_url": ("STRING", {"multiline": True, "placeholder": "https://xxx/xxx.png", "localized_name": "图片公网HTTPS链接"}),
                "prompt_ext": ("STRING", {"multiline": False, "default": "", "localized_name": "外接正向提示词"}),
                "prompt": ("STRING", {"multiline": True, "default": "流畅自然运动，人物主体稳定无变形，无画面闪烁，电影运镜，高细节", "localized_name": "正向提示词"}),
                "neg_prompt_ext": ("STRING", {"multiline": False, "default": "", "localized_name": "外接反向提示词"}),
                "neg_prompt": ("STRING", {"multiline": True, "default": "面部扭曲、画面闪烁、肢体变形、模糊、崩坏结构、水印", "localized_name": "反向提示词"}),
                "width": ("INT", {"default": 1152, "min": 512, "max": 2048, "step": 64, "localized_name": "视频宽度"}),
                "height": ("INT", {"default": 768, "min": 512, "max": 2048, "step": 64, "localized_name": "视频高度"}),
                "total_frames": ("INT", {"default": 121, "min": 81, "max": 401, "step": 8, "localized_name": "总帧数(8n+1)"}),
                "fps": ("INT", {"default": 24, "min": 12, "max": 30, "localized_name": "帧率FPS"}),
                "seed": ("INT", {"default": 123456, "localized_name": "随机种子"}),
                "seed_mode": (["randomize", "fixed"], {"default": "randomize", "localized_name": "生成后控制"}),
                "poll_interval": ("INT", {"default": 5, "min": 2, "max": 20, "localized_name": "轮询间隔(秒)"}),
            }
        }

    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("视频输出", "完整任务JSON")
    FUNCTION = "run"
    CATEGORY = "肖默定制插件"

    def run(self, api_key, img_url, prompt_ext, prompt, neg_prompt_ext, neg_prompt, width, height, total_frames, fps, seed, seed_mode, poll_interval):
        # 拼接最终提示词，外接优先
        final_prompt = prompt_ext.strip() if prompt_ext.strip() else prompt.strip()
        final_neg = neg_prompt_ext.strip() if neg_prompt_ext.strip() else neg_prompt.strip()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "image_url": img_url,
            "prompt": final_prompt,
            "negative_prompt": final_neg,
            "width": width,
            "height": height,
            "frames": total_frames,
            "fps": fps,
            "seed": seed if seed_mode == "fixed" else None
        }

        # 1. 创建生成任务
        try:
            resp = requests.post(f"{BASE_URL}/video/generate", headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            task_data = resp.json()
            task_id = task_data["task_id"]
        except requests.exceptions.RequestException as e:
            raise Exception(f"创建视频任务失败：{str(e)}")

        # 2. 轮询任务状态
        full_json = json.dumps(task_data, ensure_ascii=False, indent=2)
        while True:
            time.sleep(poll_interval)
            try:
                stat_resp = requests.get(f"{BASE_URL}/video/task/{task_id}", headers=headers, timeout=20)
                stat_resp.raise_for_status()
                task_info = stat_resp.json()
                full_json = json.dumps(task_info, ensure_ascii=False, indent=2)
                status = task_info["status"]
            except requests.exceptions.RequestException as e:
                raise Exception(f"轮询任务状态失败：{str(e)}")

            if status == "success":
                video_url = task_info["video_url"]
                break
            elif status in ["failed", "error"]:
                raise Exception(f"视频生成失败，任务信息：{full_json}")

        # 3. 下载视频转ComfyUI VIDEO张量
        try:
            vid_resp = requests.get(video_url, timeout=120)
            vid_resp.raise_for_status()
            vid_bytes = BytesIO(vid_resp.content)
        except requests.exceptions.RequestException as e:
            raise Exception(f"视频文件下载失败：{str(e)}")

        # ffmpeg读取视频帧
        probe = ffmpeg.probe(vid_bytes)
        video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
        w = int(video_stream["width"])
        h = int(video_stream["height"])

        vid_bytes.seek(0)
        out, _ = (
            ffmpeg.input("pipe:", format=probe["format"]["format_name"])
            .output("pipe:", format="rawvideo", pix_fmt="rgb24")
            .overwrite_output()
            .run(input=vid_bytes.read())
        )
        frame_np = np.frombuffer(out, np.uint8).reshape([-1, h, w, 3])
        frame_tensor = torch.from_numpy(frame_np).float() / 255.0
        video_out = frame_tensor.unsqueeze(0)

        return (video_out, full_json)

NODE_CLASS_MAPPINGS = {
    "XiaoMoAgnesVideo": XiaoMoAgnesVideo
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "XiaoMoAgnesVideo": "肖默 - Agnes V2.0 图生视频节点"
}
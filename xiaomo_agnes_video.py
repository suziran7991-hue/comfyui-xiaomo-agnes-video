import requests
import torch
import time
import json
from io import BytesIO
import av
import numpy as np

# Agnes AI 基础配置
BASE_API_URL = "https://apihub.agnes-ai.com/v1/videos"
MODEL_NAME = "agnes-video-v2.0"

class XiaoMoAgnesVideo:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "API密钥": ("STRING", {"multiline": False, "default": "sk-粘贴你的Agnes API密钥"}),
                "图片公网HTTPS链接": ("STRING", {"multiline": True, "default": "https://xxx/xxx.png"}),

                # 外接提示词设为单行，区分内置多行输入框
                "外接正向提示词": ("STRING", {"default": "", "multiline": False}),
                "正向提示词": ("STRING", {
                    "multiline": True,
                    "default": "流畅自然运动，人物主体稳定无变形，无画面闪烁，电影运镜，高细节"
                }),

                "外接反向提示词": ("STRING", {"default": "", "multiline": False}),
                "反向提示词": ("STRING", {
                    "multiline": True,
                    "default": "面部扭曲、画面闪烁、肢体变形、模糊、崩坏结构、水印"
                }),

                "视频宽度": ("INT", {"default": 1152, "min": 512, "max": 2048, "step": 64}),
                "视频高度": ("INT", {"default": 768, "min": 512, "max": 2048, "step": 64}),
                "总帧数(8n+1)": ("INT", {"default": 121, "min": 81, "max": 441, "step": 40}),
                "帧率FPS": ("INT", {"default": 24, "min": 12, "max": 30, "step": 1}),
                "随机种子": ("INT", {"default": 123456}),
                "生成后控制": (["randomize", "fixed"],),
                "轮询间隔(秒)": ("INT", {"default": 5, "min": 2, "max": 20, "step": 1}),
            }
        }

    # 双输出：1.可预览保存VIDEO流 2.完整任务JSON文本
    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("视频输出", "完整任务JSON")
    FUNCTION = "run"
    CATEGORY = "肖默定制插件"

    def download_video_to_tensor(self, video_url):
        """下载云端MP4，转为ComfyUI标准 [B, Frame, H, W, C] VIDEO张量"""
        try:
            resp = requests.get(video_url, timeout=60)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise Exception(f"视频文件下载失败：{str(e)}")

        video_bytes = BytesIO(resp.content)
        frame_list = []
        with av.open(video_bytes) as container:
            video_stream = container.streams.video[0]
            for frame in container.decode(video_stream):
                rgb_img = frame.to_image().convert("RGB")
                frame_array = np.array(rgb_img, dtype=np.float32) / 255.0
                frame_list.append(torch.from_numpy(frame_array))

        if len(frame_list) == 0:
            raise Exception("视频解析失败，未读取到任何画面帧")
        video_tensor = torch.stack(frame_list, dim=0).unsqueeze(0)
        return video_tensor

    def run(self,
            API密钥,
            图片公网HTTPS链接,
            外接正向提示词,
            正向提示词,
            外接反向提示词,
            反向提示词,
            视频宽度,
            视频高度,
            总帧数,
            帧率FPS,
            随机种子,
            生成后控制,
            轮询间隔):

        # 逻辑：外部连线有内容则优先使用外部提示词，否则使用框内手动文字
        final_pos_prompt = 外接正向提示词.strip() if 外接正向提示词.strip() else 正向提示词.strip()
        final_neg_prompt = 外接反向提示词.strip() if 外接反向提示词.strip() else 反向提示词.strip()

        # 请求头鉴权
        headers = {
            "Authorization": f"Bearer {API密钥}",
            "Content-Type": "application/json"
        }

        # 1. 构造Agnes图生视频请求体
        payload = {
            "model": MODEL_NAME,
            "image": 图片公网HTTPS链接,
            "prompt": final_pos_prompt,
            "negative_prompt": final_neg_prompt,
            "width": 视频宽度,
            "height": 视频高度,
            "num_frames": 总帧数,
            "frame_rate": 帧率FPS,
            "seed": 随机种子 if 生成后控制 == "fixed" else None
        }

        # 2. 创建异步视频任务，捕获网络异常
        try:
            create_resp = requests.post(BASE_API_URL, headers=headers, json=payload, timeout=30)
            create_resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise Exception(f"创建视频任务请求失败：{str(e)}")

        task_data = create_resp.json()
        task_id = task_data["id"]

        # 3. 循环轮询任务状态，直到完成/失败
        final_task_data = None
        while True:
            try:
                poll_resp = requests.get(f"{BASE_API_URL}/{task_id}", headers=headers, timeout=30)
                poll_resp.raise_for_status()
            except requests.exceptions.RequestException as e:
                raise Exception(f"轮询任务状态请求失败：{str(e)}")

            final_task_data = poll_resp.json()
            status = final_task_data["status"]

            if status == "completed":
                break
            if status in ["failed", "cancelled"]:
                err_msg = final_task_data.get("error", {}).get("message", "未知生成失败")
                raise Exception(f"视频生成任务失败：{err_msg}")
            # 等待指定秒数再次轮询
            time.sleep(轮询间隔)

        # 4. 提取MP4下载直链，增加空值校验防止崩溃
        mp4_url = final_task_data.get("remixed_from_video_id")
        if not mp4_url:
            raise Exception("任务已完成，但未获取到视频下载链接，请查看完整任务JSON排查详情")

        # 完整JSON转为字符串用于第二个输出端口
        full_json_str = json.dumps(final_task_data, ensure_ascii=False, indent=2)
        # 下载视频转为ComfyUI视频张量
        video_output_tensor = self.download_video_to_tensor(mp4_url)

        return (video_output_tensor, full_json_str)

# 插件节点注册
NODE_CLASS_MAPPINGS = {
    "XiaoMoAgnesVideo": XiaoMoAgnesVideo
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "XiaoMoAgnesVideo": "肖默 - Agnes V2.0 图生视频节点"
}
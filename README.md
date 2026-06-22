# comfyui-xiaomo-agnes-video
## 肖默定制 Agnes V2.0 图生视频API节点
专属标识：肖默，适配 Agnes-Video-V2.0 官方异步API，一键自动化工作流，无需拼接多个请求节点

## 功能介绍
1. 图生视频能力，适配新版 apihub.agnes-ai.com 域名
2. 内置帧数规则校验，强制遵循 `8n+1` 规范（81/121/241/441），提前拦截错误参数
3. 自动轮询查询生成进度，排队、渲染全程自动等待，无需手动多次GET请求
4. 界面全节点携带专属标记「肖默」，私有化辨识度高，便于区分自用定制插件
5. 一键输出视频MP4直链，搭配通用下载节点即可保存本地视频文件

## 安装教程
### 方式1：ComfyUI Manager 一键安装（插件上架后可用）
1. 打开 ComfyUI Manager → Install Custom Nodes
2. 搜索插件名：`xiaomo-agnes-video`
3. 点击 Install，等待自动安装依赖 `requests`，重启ComfyUI生效

### 方式2：手动离线安装
1. 打开 ComfyUI 目录下 `custom_nodes` 文件夹
2. 执行Git克隆命令：
```bash
git clone https://github.com/suziran7991-hue/comfyui-xiaomo-agnes-video.git
```
## 使用说明
在输入框 肖默_API密钥 填写你个人的 Agnes 平台 API Key
肖默_图片公网HTTPS链接 填入图床上传后的公开图片地址，本地文件无法识别
帧数仅支持标准数值：81、121、241、441，其他数字会弹窗提示错误
正向提示词描述画面运动、镜头风格；反向提示词屏蔽崩坏、闪烁、变形等问题
输出端口 1 为完整 MP4 视频下载链接，端口 2 为任务完整返回 JSON，用于排查报错
依赖说明
插件自动依赖 requests 网络库，Manager 安装时会自动部署，手动安装缺失可执行：
```bash
python_embeded\python.exe -m pip install requests>=2.30.0
```
# 自愿捐赠支持
本插件完全免费开源，无任何功能锁定、付费门槛，所有人均可无限制下载、使用、二次修改。
如果你觉得本节点大幅简化了 Agnes 视频 API 调用流程，节约了大量调试时间，愿意支持作者持续更新维护、优化功能、适配新版本接口，可以自愿捐赠。

所有捐赠仅作为创作激励，不强制、不捆绑任何功能权益，无论是否捐赠都能使用全部功能。
## 后续更新规划
1. 新增文生视频、多图关键帧动画双模式节点
2. 适配Agnes官方后续更新参数
3. 优化报错提示、增加参数预设模板
4. 兼容更多分辨率、帧率组合
### 捐赠渠道
作者：肖默
GitHub账号：suziran7991-hue
- 渠道：微信捐赠
![微信捐赠收款二维码](./donate_qrcode.png)
## 开源协议
本项目基于 MIT 开源协议，可自由分发、修改、商用，详情查看仓库根目录 `LICENSE` 文件

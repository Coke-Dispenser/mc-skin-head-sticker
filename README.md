# 方块合照 · MC Skin Head Sticker

把 Minecraft 皮肤头像贴到真实合照上的多人协作网页工具，提供管理员与共享成员两种角色。

A collaborative web app for placing Minecraft skin heads onto group photos.

使用 Vue 3、Konva；后端使用 FastAPI、Pillow 和 SQLite，通过 Docker Compose 部署。

管理员上传多张 JPG、PNG 或 WebP 合照。所有登录成员均可添加、拖动、旋转、缩放或移除头像。头像支持 Java 版正版 ID 获取和自定义图片上传。操作完成后自动保存，通过「刷新合照」获取他人的修改。同一头像发生版本冲突时提示刷新。

每张照片的首个头像确定后续头像的默认尺寸。「全部统一为这个大小」会更新这张照片上的所有头像。导出为原分辨率 PNG，仅包含合照和头像。

成员列表按图层从上到下排列，上面的头像覆盖下面的头像。选中头像可「上移一层」「下移一层」「置顶」「置底」，电脑端也可以拖动列表左侧手柄排序。新头像默认置于最上层；选中、移动、缩放或旋转不会改变层级。图层顺序自动保存，并同步用于缩略图与导出。升级时会保留原有照片的叠放顺序。

## 快速部署

准备 Docker Compose、Node.js 22.12+ 和 Python 3.12+，在项目根目录执行：

```sh
cd frontend
npm ci
npm run build
cd ..
python scripts/setup_env.py
docker compose up -d --build
```

配置脚本会询问管理员与成员的账号、密码，创建本地 `.env`，只保存密码哈希。已有 `.env` 时不会覆盖。也可参考 `.env.example` 手工配置；其中的空哈希需要自行填写。

默认访问 `http://localhost:8081`；服务器部署时使用服务器地址，并放行对应端口。修改 `.env` 中的 `PHOTO_PORT` 可更换端口。通过 HTTPS 反向代理提供服务时，将 `COOKIE_SECURE` 设为 `1`。

Dockerfile 使用生成的 `frontend/dist`。更新前端后需重新构建前端，再执行 `docker compose up -d --build`。服务端依赖在镜像构建时安装。

在项目根目录配置 .env，字段为 ADMIN_USER、ADMIN_HASH、MEMBER_USER、MEMBER_HASH、PHOTO_PORT。密码哈希格式为 salt_hex:digest_hex，算法为 PBKDF2-HMAC-SHA256，260000 次迭代。浏览器会话为 HttpOnly Cookie，有效期 7 天。

运行 docker compose up -d --build。使用 docker compose stop 停止，docker compose start 启动，docker compose logs --tail=100 查看日志。

## 数据

SQLite、原图和头像存放于 Docker 数据卷 bcl-photo_studio-data，挂载到 /data。备份时先停止容器，再归档该卷目录；同时保留服务器项目中的 .env。重建容器会继续使用该数据卷。

仓库不包含生产密码、用户合照或数据库。不要提交 `.env`，不要使用 `docker compose down -v` 删除持久化数据卷。共享成员账号可以修改、删除头像及导出所有合照。

## 验证

安装 backend/requirements.txt 后，在项目根目录运行 python -m unittest backend.test_app -v。测试使用临时数据目录，验证权限、图片访问、保存冲突、并发修改与导出。

## 许可证

采用 [MIT License](LICENSE)。

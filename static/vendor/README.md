# static/vendor/

此目录存放**本地托管的第三方前端库**，不进入 git。

## 为什么要本地托管

页面早期直接从 CDN 引入 Tailwind。对局域网自托管工具来说，那是一个必须离开本机的
请求：一个会静默失败的依赖，而且一次不必要的对外连接。改为本地托管后，
**页面零外部请求**。

## Tailwind

`tailwind.js` 是 Tailwind CSS 的 Play CDN 构建（浏览器端即时编译）。
文件不在仓库中，需要获取一次：

```bash
mkdir -p static/vendor
curl -sL -o static/vendor/tailwind.js https://cdn.tailwindcss.com
```

获取后由 `static/index.html` 引用：

```html
<script src="vendor/tailwind.js"></script>
```

若文件缺失，页面仍能正常加载——外壳样式来自 `static/css/app.css`，不依赖 Tailwind；
只是无法使用它的工具类。缺失时不会报错，因此若发现工具类无效，先确认该文件存在。

## 关于 Play CDN

Tailwind 官方明确说明 Play CDN **仅供开发使用**，它在页面加载时于浏览器内编译样式。
对于自托管单人工具这是可接受的取舍，换来的是无需构建步骤、无 `node_modules`。
若将来面向多人使用，应改为编译产物（`npx @tailwindcss/cli`），本文件届时可直接替换为
构建输出。

## 校验

`tools/check_ui.py` 会解析 `static/js/` 下的每个 JS 文件，**不包含**本目录（第三方代码
不归本项目维护）。

```bash
python -m tools.check_ui
```
"""把 pub/ 发布到阿里云 OSS（香港节点，免备案），带 gzip 与缓存策略。

    python web/deploy_aliyun.py            # 上传 + 回读校验 + 匿名访问检查
    python web/deploy_aliyun.py --dry-run  # 只打印将要做什么，不发写请求

为什么单独写一个而不是直接用 skill 里的 deploy_oss.py：
1. 那个脚本不压 gzip。tool.html 有 1.9MB（pdf.js + pdf-lib 全内联），
   香港到大陆不压缩要 8 秒；gzip 后 583KB，2 秒出头。
2. 这里把「域名 + 桶 + 证书」写死成本项目的常量，避免每次手敲参数敲错。

前提（都在阿里云控制台一次性配好，脚本不碰）：
- 桶 a3-paper-split-hk / 香港地域 / ACL public-read / BlockPublicAccess 已关；
- 静态网站托管 Index=index.html Error=404.html；
- CNAME  a3 -> a3-paper-split-hk.oss-cn-hongkong.aliyuncs.com
- 自定义域名 a3.ydtgo.top 绑定 + 泛域名证书（cert_id 见 CERT_ID）

凭证只从环境变量读，不落盘、不打印：
    ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET
"""
import argparse
import gzip
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'pub')

BUCKET = 'a3-paper-split-hk'
REGION = 'cn-hongkong'
SITE = 'https://a3.ydtgo.top'

# 文件 -> (Content-Type, Cache-Control)
# html 一律 no-cache：改完立刻生效，否则手机上会拿着旧页面用
RULES = {
    'index.html': ('text/html; charset=utf-8', 'no-cache'),
    'tool.html': ('text/html; charset=utf-8', 'no-cache'),
    '404.html': ('text/html; charset=utf-8', 'no-cache'),
}


def clear_proxy():
    """本机常驻一个本地代理并导出了 HTTP(S)_PROXY，oss2 底层的 requests 会老实走它，
    代理一抽风就报 ProxyError / Tunnel connection failed: 502。
    这里直接清掉，纯命令行工具不需要代理。"""
    dropped = []
    for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        if os.environ.pop(k, None) is not None:
            dropped.append(k)
    if dropped:
        print('[i] 已清除代理环境变量（本机代理会搞挂 oss2）：%s' % ', '.join(dropped))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-gzip', action='store_true', help='跳过 gzip（默认压）')
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        print('× 找不到 %s，先跑：python web/build_public.py' % SRC)
        return 2

    files = [f for f in sorted(os.listdir(SRC)) if f in RULES]
    missing = [f for f in RULES if f not in files]
    if missing:
        print('× pub/ 缺文件：%s' % ', '.join(missing))
        return 2

    clear_proxy()
    import oss2

    ak = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
    sk = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
    if not ak or not sk:
        print('× 缺环境变量 ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET')
        return 2

    bucket = oss2.Bucket(oss2.Auth(ak, sk), 'https://oss-%s.aliyuncs.com' % REGION, BUCKET)
    print('桶=%s  地域=%s' % (BUCKET, REGION))

    total_raw = total_sent = 0
    for name in files:
        path = os.path.join(SRC, name)
        raw = open(path, 'rb').read()
        ctype, cache = RULES[name]
        headers = {'Content-Type': ctype, 'Cache-Control': cache}

        body = raw
        if not args.no_gzip and name.endswith('.html'):
            body = gzip.compress(raw, 9)
            headers['Content-Encoding'] = 'gzip'

        md5 = hashlib.md5(body).hexdigest()
        ratio = 100.0 * (1 - len(body) / len(raw)) if raw else 0
        total_raw += len(raw)
        total_sent += len(body)

        if args.dry_run:
            print('  [dry-run] PUT %-12s %8.1f KB -> %7.1f KB (-%.0f%%)'
                  % (name, len(raw) / 1024, len(body) / 1024, ratio))
            continue

        bucket.put_object(name, body, headers=headers)
        # 回读比对 md5：比字节数可靠，能抓到「传了但内容不对」
        got = bucket.head_object(name).etag.strip('"').lower()
        flag = '✓' if got == md5 else '×'
        print('  %s %-12s %8.1f KB -> %7.1f KB (-%.0f%%)  md5=%s'
              % (flag, name, len(raw) / 1024, len(body) / 1024, ratio, got[:12]))
        if got != md5:
            print('    线上 md5 %s != 本地 %s' % (got, md5))
            return 1

    print('合计 %.1f KB -> %.1f KB' % (total_raw / 1024, total_sent / 1024))
    if args.dry_run:
        print('（dry-run，未发送任何写请求）')
        return 0

    # 匿名访问检查：不带任何凭证、绕开代理，模拟真实访客
    print('匿名访问检查：')
    import urllib.request
    for key in ('index.html', 'tool.html'):
        url = 'https://%s.oss-%s.aliyuncs.com/%s' % (BUCKET, REGION, key)
        req = urllib.request.Request(url, headers={'User-Agent': 'deploy-check',
                                                   'Accept-Encoding': 'identity'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                n = len(r.read())
                print('  ✓ %s HTTP %d  %d 字节' % (url, r.status, n))
        except Exception as e:
            print('  × %s 失败：%s' % (url, str(e)[:120]))
            print('    多半是桶 ACL 不是 public-read，或 BlockPublicAccess 又开了')
            return 1

    print('线上：%s' % SITE)
    return 0


if __name__ == '__main__':
    sys.exit(main())

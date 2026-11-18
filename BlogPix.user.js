// ==UserScript==
// @name         BlogPix v3.1 — 網頁貼文圖片下載
// @namespace    blogpix
// @version      3.1
// @description  滑鼠移到圖片上點「⬇ BlogPix」，或框選區塊後按快捷鍵，下載整篇貼文圖片（不需要網址）
// @match        http://*/*
// @match        https://*/*
// @exclude      http://127.0.0.1:*/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @noframes
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  // 需與 BlogPix 的連線埠一致（預設 8765）。快捷鍵、儲存位置、檔名、格式都在 BlogPix 視窗裡設定。
  const BASE = 'http://127.0.0.1:8765';

  const isWeibo = /(^|\.)weibo\.(com|cn)$/.test(location.hostname);
  let mx = 0, my = 0, busy = false, lastSeen = null, delay = 500;
  let connected = false, showButton = true;
  document.addEventListener('mousemove', (e) => { mx = e.clientX; my = e.clientY; }, { passive: true });

  // ---------- 提示訊息 ----------
  function toast(msg, ms = 3000) {
    let box = document.getElementById('blogpix-toast');
    if (!box) {
      box = document.createElement('div');
      box.id = 'blogpix-toast';
      box.style.cssText = 'position:fixed;right:20px;bottom:24px;z-index:2147483647;max-width:380px;' +
        'padding:10px 16px;border-radius:8px;background:rgba(30,30,30,.95);color:#fff;' +
        'font:14px/1.5 sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.4);white-space:pre-line;';
      document.body.appendChild(box);
    }
    box.textContent = msg;
    box.style.display = 'block';
    clearTimeout(box._t);
    box._t = setTimeout(() => { box.style.display = 'none'; }, ms);
  }

  // ---------- 與 BlogPix 溝通 ----------
  function gm(method, path, body, timeout) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method, url: BASE + path,
        headers: { 'Content-Type': 'application/json', 'X-BlogPix': '1' },
        data: body ? JSON.stringify(body) : undefined,
        timeout: timeout || 5000,
        onload: (r) => { try { resolve(JSON.parse(r.responseText)); } catch (e) { reject(e); } },
        onerror: () => reject(new Error('offline')),
        ontimeout: () => reject(new Error('timeout')),
      });
    });
  }

  // 輪詢：BlogPix 的快捷鍵被按下時，由目前停留的分頁負責處理
  async function poll() {
    let wait = 1000;
    if (document.hasFocus() && document.visibilityState === 'visible') {
      try {
        const d = await gm('GET', '/poll');
        delay = 500;
        connected = true;
        showButton = d.button !== false;
        if (lastSeen === null) lastSeen = (d.age < 3 && d.trigger > 0) ? d.trigger - 1 : d.trigger;
        if (d.trigger > lastSeen) {
          lastSeen = d.trigger;
          if (d.age < 3) run(null);
        }
      } catch (e) {
        connected = false;
        delay = Math.min(delay * 2, 8000); // BlogPix 沒開時放慢頻率
      }
      wait = delay;
    }
    setTimeout(poll, wait);
  }

  // ---------- 找到滑鼠所在的貼文 ----------
  function elementUnderMouse() {
    const els = document.elementsFromPoint(mx, my);
    return els.find((e) => e !== document.documentElement && e !== document.body) || document.body;
  }

  // ===== 微博：精準解析 =====
  function weiboContainer(start) {
    const list = start ? [start] : document.elementsFromPoint(mx, my);
    for (const el of list) {
      const c = el.closest && el.closest('article, [class*="Feed_wrap"], [class*="Feed_body"]');
      if (c) return c;
    }
    return null;
  }

  function weiboPostId(container) {
    const re = /\/(\d{5,})\/([A-Za-z0-9]{8,})(?:[?#]|$)/;
    if (container) {
      for (const a of container.querySelectorAll('a[href]')) {
        const m = (a.getAttribute('href') || '').match(re);
        if (m) return m[2];
      }
    }
    const m = location.pathname.match(/^\/(\d{5,})\/([A-Za-z0-9]{8,})/);
    return m ? m[2] : null;
  }

  async function weiboPicsFromApi(id) {
    const r = await fetch('/ajax/statuses/show?id=' + encodeURIComponent(id) + '&locale=zh-CN',
      { credentials: 'include' });
    const d = await r.json();
    let post = d;
    if (!(post.pic_ids && post.pic_ids.length) && d.retweeted_status) post = d.retweeted_status;
    const infos = post.pic_infos || {};
    return (post.pic_ids || []).map((pid) => {
      const i = infos[pid] || {};
      for (const k of ['original', 'largest', 'mw2000', 'large']) {
        if (i[k] && i[k].url) return i[k].url;
      }
      return 'https://wx1.sinaimg.cn/oslarge/' + pid + '.jpg';
    });
  }

  function weiboPicsFromDom(container) {
    const out = [];
    container.querySelectorAll('img').forEach((img) => {
      const s = img.currentSrc || img.src || '';
      const m = s.match(/^https?:\/\/((?:wx|ww)\d)\.sinaimg\.cn\/[^/]+\/([^/?]+\.(?:jpe?g|png|gif|webp))/i);
      if (m) {
        const u = 'https://' + m[1] + '.sinaimg.cn/large/' + m[2];
        if (!out.includes(u)) out.push(u);
      }
    });
    return out;
  }

  async function weiboJob(start, picked) {
    const container = weiboContainer(start);
    const id = weiboPostId(container);
    if (!id) {
      toast('找不到貼文。\n請把滑鼠移到貼文的圖片上，或先框選貼文區塊。');
      return null;
    }
    flash(container);
    let urls = [];
    if (picked && picked.length) {
      // 使用者框選了特定圖片：只下載選到的（以圖片 ID 對應 API 的原圖網址）
      const ids = picked.map((i) => ((i.currentSrc || i.src || '').match(/\/([^/?]+)\.(?:jpe?g|png|gif|webp)/i) || [])[1]).filter(Boolean);
      let all = [];
      try { all = await weiboPicsFromApi(id); } catch (e) { /* 改用備案 */ }
      urls = all.filter((u) => ids.some((x) => u.includes(x)));
      if (!urls.length) {
        const tmp = document.createElement('div');
        picked.forEach((i) => tmp.appendChild(i.cloneNode()));
        urls = weiboPicsFromDom(tmp);
      }
    } else {
      try { urls = await weiboPicsFromApi(id); } catch (e) { /* 改用備案 */ }
      if (!urls.length && container) urls = weiboPicsFromDom(container);
    }
    if (!urls.length) { toast('這篇貼文沒有圖片，或無法取得。'); return null; }
    return { urls, post: id, referer: 'https://weibo.com/', site: 'weibo.com' };
  }

  // ===== 一般網站：盡力解析（取滑鼠所在區塊內的圖片）=====
  const SKIP = /(avatar|icon|logo|emoji|sprite|badge|favicon|loading|placeholder|blank|pixel|spacer)/i;

  function bestSrc(img) {
    const a = img.closest('a[href]');
    if (a && /\.(jpe?g|png|webp|gif|bmp|avif)(\?|#|$)/i.test(a.getAttribute('href') || '')) return a.href; // 超連結指向大圖
    for (const k of ['data-original', 'data-src', 'data-lazy-src', 'data-actualsrc']) {
      if (img.getAttribute(k)) return img.getAttribute(k);
    }
    const ss = img.getAttribute('srcset') || img.getAttribute('data-srcset');
    if (ss) {
      let best = null, bw = -1;
      ss.split(',').forEach((p) => {
        const b = p.trim().split(/\s+/);
        const w = b[1] ? parseFloat(b[1]) : 0;
        if (b[0] && w >= bw) { best = b[0]; bw = w; }
      });
      if (best) return best;
    }
    return img.currentSrc || img.src || '';
  }

  // 太小或看起來像頭像／圖示的圖片不算
  function qualifies(img) {
    const r = img.getBoundingClientRect();
    const w = img.naturalWidth || r.width, h = img.naturalHeight || r.height;
    if ((w && w < 120) || (h && h < 80)) return false;
    return !SKIP.test((img.className || '') + ' ' + (img.alt || ''));
  }

  function collectImgs(root) {
    const out = [];
    root.querySelectorAll('img').forEach((img) => {
      if (!qualifies(img)) return;
      let u = bestSrc(img);
      if (!u || /^(data|blob):/i.test(u) || SKIP.test(u)) return;
      try { u = new URL(u, location.href).href; } catch (e) { return; }
      if (!out.includes(u)) out.push(u);
    });
    return out;
  }

  function hash36(s) {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36).slice(0, 6);
  }

  function urlsOf(imgs) {
    const out = [];
    imgs.forEach((img) => {
      let u = bestSrc(img);
      if (!u || /^(data|blob):/i.test(u) || SKIP.test(u)) return;
      try { u = new URL(u, location.href).href; } catch (e) { return; }
      if (!out.includes(u)) out.push(u);
    });
    return out;
  }

  function genericJob(start, picked) {
    if (picked && picked.length) {
      const urls = urlsOf(picked);
      if (urls.length) {
        return { urls, post: location.hostname + '_' + hash36(location.pathname + location.search + urls[0]),
                 referer: location.href, site: location.hostname };
      }
    }
    let el = start || elementUnderMouse(), best = null;
    while (el && el !== document.documentElement) {
      const imgs = collectImgs(el);
      // 遇到語意上的「一篇貼文」區塊（article）就停，不再往外擴張
      if (imgs.length >= 1 && el.matches && el.matches('article, [role="article"]')) { best = { el, imgs }; break; }
      if (imgs.length >= 2) {
        if (el === document.body && best) break; // 往上已是整頁，改用較小的單張圖結果
        best = { el, imgs };
        break;
      }
      if (imgs.length === 1 && !best) best = { el, imgs };
      el = el.parentElement;
    }
    if (!best) { toast('滑鼠附近找不到圖片。\n請把滑鼠移到貼文或圖片區塊上再按快捷鍵。'); return null; }
    if (best.el === document.body && best.imgs.length > 40) {
      toast('這頁圖片太多，請把滑鼠移到貼文區塊內再按快捷鍵。');
      return null;
    }
    flash(best.el);
    return { urls: best.imgs, post: location.hostname + '_' + hash36(location.pathname + location.search + best.imgs[0]),
             referer: location.href, site: location.hostname };
  }

  // 使用者用滑鼠框選的區塊：回傳 {root, imgs}（imgs 為選取範圍內的圖片，可能為空）
  function selectedRegion() {
    const sel = window.getSelection && window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const ranges = [];
    for (let i = 0; i < sel.rangeCount; i++) ranges.push(sel.getRangeAt(i));
    const n = ranges[0].commonAncestorContainer;
    const root = n.nodeType === 1 ? n : n.parentElement;
    if (!root) return null;
    const hit = (img) => ranges.some((r) => { try { return r.intersectsNode(img); } catch (e) { return false; } });
    const imgs = Array.from(root.querySelectorAll('img')).filter((i) => hit(i) && qualifies(i));
    if (root.tagName === 'IMG' && qualifies(root) && !imgs.length) imgs.push(root);
    return { root, imgs };
  }

  function flash(el) {
    if (!el || !el.style) return;
    const old = el.style.outline;
    el.style.outline = '2px solid #ff8200';
    setTimeout(() => { el.style.outline = old; }, 1200);
  }

  // ---------- 主流程 ----------
  // startEl：由浮動按鈕傳入的圖片元素；快捷鍵觸發時為 null（先看有無框選，否則用滑鼠位置）
  async function run(startEl) {
    if (busy) return;
    busy = true;
    try {
      toast('取得圖片清單中…', 8000);
      let start = startEl || null, picked = null;
      if (!start) {
        const region = selectedRegion();
        if (region) { start = region.root; picked = region.imgs; }
      }
      const job = isWeibo ? await weiboJob(start, picked) : genericJob(start, picked);
      if (!job) return;
      toast('共 ' + job.urls.length + ' 張，交給 BlogPix 下載…\n（若設定為「每次詢問」，請到 BlogPix 視窗選擇資料夾）', 120000);
      const res = await gm('POST', '/save', job, 900000);
      if (res.cancelled) toast('已取消（未選擇資料夾）');
      else if (res.ok) toast('完成：新存 ' + res.saved + ' 張，略過 ' + res.skipped + ' 張' +
        (res.failed ? '，失敗 ' + res.failed + ' 張' : '') + '\n' + res.folder, 6000);
      else toast('BlogPix 回報錯誤：' + (res.error || '未知'));
    } catch (e) {
      toast(e.message === 'offline'
        ? '連不上 BlogPix。\n請先開啟 BlogPix 程式（視窗保持開著）。'
        : '發生錯誤：' + e.message);
    } finally {
      busy = false;
    }
  }

  // ---------- 滑鼠移到圖片上：浮出「⬇ BlogPix」按鈕 ----------
  let btn = null, btnImg = null, hideT = null, lastMove = 0;

  function ensureBtn() {
    if (btn) return btn;
    btn = document.createElement('div');
    btn.id = 'blogpix-btn';
    btn.textContent = '⬇ BlogPix';
    btn.title = '下載這篇貼文的全部圖片';
    btn.style.cssText = 'position:fixed;z-index:2147483646;display:none;padding:5px 11px;border-radius:6px;' +
      'background:#ff8200;color:#fff;font:bold 13px/1.4 sans-serif;cursor:pointer;user-select:none;' +
      'box-shadow:0 2px 8px rgba(0,0,0,.45);';
    btn.addEventListener('mousedown', (e) => { e.preventDefault(); e.stopPropagation(); });
    btn.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      const t = btnImg;
      btn.style.display = 'none';
      if (t) run(t);
    });
    btn.addEventListener('mouseenter', () => clearTimeout(hideT));
    btn.addEventListener('mouseleave', scheduleHide);
    document.body.appendChild(btn);
    return btn;
  }

  function scheduleHide() {
    clearTimeout(hideT);
    hideT = setTimeout(() => { if (btn) btn.style.display = 'none'; btnImg = null; }, 500);
  }

  function imgUnderPointer(x, y) {
    return document.elementsFromPoint(x, y).find((el) => {
      if (el.tagName !== 'IMG') return false;
      return qualifies(el);
    });
  }

  document.addEventListener('mousemove', (e) => {
    const now = Date.now();
    if (now - lastMove < 120) return;
    lastMove = now;
    if (!connected || !showButton || busy) { if (btn) btn.style.display = 'none'; return; }
    if (e.target === btn) return;
    const img = imgUnderPointer(e.clientX, e.clientY);
    if (!img) { scheduleHide(); return; }
    clearTimeout(hideT);
    btnImg = img;
    const b = ensureBtn(), r = img.getBoundingClientRect();
    b.style.left = Math.max(4, Math.min(window.innerWidth - 110, r.left + 8)) + 'px';
    b.style.top = Math.max(4, Math.min(window.innerHeight - 36, r.top + 8)) + 'px';
    b.style.display = 'block';
  }, { passive: true });

  poll();
})();

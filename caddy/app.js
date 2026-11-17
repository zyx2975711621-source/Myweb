/* ---------- shared helpers ---------- */
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

var RATING_NAMES = { 5: "夯", 4: "顶级", 3: "人上人", 2: "NPC", 1: "拉完了" };
var RATING_CLASS = { 5: "lvl-5", 4: "lvl-4", 3: "lvl-3", 2: "lvl-2", 1: "lvl-1" };

function ratingLabel(n) { return RATING_NAMES[n] || n; }

function renderRatingPill(n) {
  return '<span class="rating-pill ' + (RATING_CLASS[n] || "") + '">' + escapeHtml(ratingLabel(n)) + "</span>";
}

async function api(path, opts) {
  opts = opts || {};
  var init = { method: opts.method || "GET", credentials: "same-origin" };
  if (opts.json !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  } else if (opts.form !== undefined) {
    init.body = opts.form;
  }
  var res = await fetch(path, init);
  var data = null;
  try { data = await res.json(); } catch (e) { /* ignore */ }
  if (!res.ok || !data || data.ok === false) {
    var msg = (data && data.message) || ("请求失败(" + res.status + ")");
    if (res.status === 401 && !opts.silent401) {
      toast(msg + "，正在跳转登录…");
      setTimeout(function () { location.href = "/login.html"; }, 900);
      throw new Error(msg);
    }
    throw new Error(msg);
  }
  return data;
}

var toastTimer = null;
function toast(msg) {
  var el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.style.display = "none"; }, 2200);
}

/* ---------- nav: 美食展示 | + | 我的收藏 居中，右侧小圆 ---------- */
var ME = null;

async function initPage() {
  try { ME = (await api("/api/me", { silent401: true })).user; } catch (e) { ME = null; }
  renderNav();
  initTrail();
  if (typeof pageInit === "function") pageInit();
}

function renderNav() {
  var root = document.getElementById("nav-root");
  if (!root) return;
  var active = document.body.getAttribute("data-page") || "";
  var role = ME ? ME.role : "guest";
  var plus = (role === "editor" || role === "admin")
    ? '<a class="plus-btn" href="/upload.html" title="上传美食记录">+</a>' : "";
  var center =
    '<a class="nav-link' + (active === "foods" ? " active" : "") + '" href="/">美食展示</a>' +
    plus +
    '<a class="nav-link' + (active === "todo" ? " active" : "") + '" href="/todo.html">我的待吃</a>';
  var initial = ME ? escapeHtml(ME.username.charAt(0).toUpperCase()) : "👤";
  var menu;
  if (ME) {
    var roleNames = { admin: "管理员", editor: "编辑者", user: "普通用户" };
    menu =
      '<div class="menu-head"><b>' + escapeHtml(ME.username) + '</b><span class="role-tag ' + role + '">' + (roleNames[role] || role) + "</span></div>" +
      '<a class="menu-item" href="/profile.html">👤 个人主页</a>' +
      '<a class="menu-item" href="/favorites.html">⭐ 我的收藏</a>' +
      (role === "admin" ? '<a class="menu-item" href="/admin.html">⚙️ 后台管理</a>' : "") +
      '<button class="menu-item danger" id="nav-logout" type="button">⎋ 退出登录</button>';
  } else {
    menu =
      '<a class="menu-item" href="/login.html">🔑 登录</a>' +
      '<a class="menu-item" href="/register.html">✨ 注册</a>';
  }
  root.innerHTML =
    '<nav class="site-nav">' +
    '<div class="nav-center">' + center + "</div>" +
    '<div class="avatar-wrap">' +
    '<button class="avatar-btn" id="avatar-btn" type="button">' + initial + "</button>" +
    '<div class="avatar-menu" id="avatar-menu">' + menu + "</div>" +
    "</div></nav>";
  var btn = document.getElementById("avatar-btn");
  var menuEl = document.getElementById("avatar-menu");
  btn.addEventListener("click", function (e) {
    e.stopPropagation();
    menuEl.classList.toggle("open");
  });
  document.addEventListener("click", function (e) {
    if (menuEl.classList.contains("open") && !menuEl.contains(e.target) && e.target !== btn) {
      menuEl.classList.remove("open");
    }
  });
  var lo = document.getElementById("nav-logout");
  if (lo) lo.addEventListener("click", async function () {
    await api("/api/logout", { method: "POST", silent401: true });
    location.href = "/";
  });
}

/* ---------- mouse trail ---------- */
function initTrail() {
  var lastX = -999, lastY = -999, count = 0;
  document.addEventListener("pointermove", function (e) {
    if (e.pointerType && e.pointerType !== "mouse") return;
    var dx = e.clientX - lastX, dy = e.clientY - lastY;
    if (dx * dx + dy * dy < 36) return; // ~6px 距离生成一个，短而密且轻微重叠
    lastX = e.clientX; lastY = e.clientY;
    var d = document.createElement("div");
    d.className = "trail-dot";
    d.style.left = e.clientX + "px";
    d.style.top = e.clientY + "px";
    d.style.animationDelay = (count % 5) * 40 + "ms"; // 依次延迟跟随
    count++;
    document.body.appendChild(d);
    d.addEventListener("animationend", function () { d.remove(); });
    var dots = document.querySelectorAll(".trail-dot");
    if (dots.length > 90) dots[0].remove();
  });
}

/* ---------- lightbox ---------- */
var lbImgs = [], lbIdx = 0;

function openLightbox(srcs, idx) {
  lbImgs = srcs; lbIdx = idx;
  var lb = document.getElementById("lightbox");
  if (!lb) {
    lb = document.createElement("div");
    lb.id = "lightbox";
    lb.innerHTML =
      '<button class="lb-btn lb-prev" type="button">‹</button>' +
      '<img alt="预览图片">' +
      '<button class="lb-btn lb-next" type="button">›</button>' +
      '<button class="lb-btn lb-close" type="button">✕</button>' +
      '<div class="lb-count"></div>';
    document.body.appendChild(lb);
    lb.querySelector(".lb-prev").addEventListener("click", function (e) { e.stopPropagation(); stepLightbox(-1); });
    lb.querySelector(".lb-next").addEventListener("click", function (e) { e.stopPropagation(); stepLightbox(1); });
    lb.querySelector(".lb-close").addEventListener("click", closeLightbox);
    lb.addEventListener("click", function (e) { if (e.target === lb) closeLightbox(); });
    document.addEventListener("keydown", function (ev) {
      if (!lb.classList.contains("open")) return;
      if (ev.key === "Escape") closeLightbox();
      if (ev.key === "ArrowLeft") stepLightbox(-1);
      if (ev.key === "ArrowRight") stepLightbox(1);
    });
  }
  showLightbox();
  lb.classList.add("open");
}

function showLightbox() {
  var lb = document.getElementById("lightbox");
  lb.querySelector("img").src = lbImgs[lbIdx];
  lb.querySelector(".lb-count").textContent = (lbIdx + 1) + " / " + lbImgs.length;
  var multi = lbImgs.length > 1;
  lb.querySelector(".lb-prev").style.display = multi ? "" : "none";
  lb.querySelector(".lb-next").style.display = multi ? "" : "none";
}

function stepLightbox(d) {
  lbIdx = (lbIdx + d + lbImgs.length) % lbImgs.length;
  showLightbox();
}

function closeLightbox() {
  var lb = document.getElementById("lightbox");
  if (lb) { lb.classList.remove("open"); lb.querySelector("img").src = ""; }
}

/* ---------- 美食卡片（列表/收藏页共用，支持卡片上直接点赞收藏） ---------- */
function foodCardHtml(f) {
  var cover = f.cover
    ? '<img class="food-cover" loading="lazy" src="/uploads/' + escapeHtml(f.cover) + '" alt="">'
    : '<div class="food-cover-empty">🍜</div>';
  var acts;
  if (ME) {
    acts =
      '<button class="card-act want' + (f.wanted ? " wanted" : "") + '" data-act="want" data-id="' + f.id + '" type="button">' +
      (f.wanted ? "✓ 待吃" : "＋ 待吃") + (f.want_count > 0 ? "·" + f.want_count : "") + "</button>" +
      '<button class="card-act' + (f.liked ? " liked" : "") + '" data-act="like" data-id="' + f.id + '" type="button">' +
      (f.liked ? "♥" : "♡") + " " + f.like_count + "</button>" +
      '<button class="card-act' + (f.favorited ? " faved" : "") + '" data-act="fav" data-id="' + f.id + '" type="button">' +
      (f.favorited ? "⭐" : "☆") + "</button>";
  } else {
    acts =
      '<button class="card-act" data-act="login" type="button">＋ 待吃</button>' +
      '<button class="card-act" data-act="login" type="button">♡ ' + f.like_count + "</button>" +
      '<button class="card-act" data-act="login" type="button">☆</button>';
  }
  return '<div class="food-card" data-id="' + f.id + '">' + cover +
    '<div class="food-card-body">' +
    '<div class="food-title">' + escapeHtml(f.title) + "</div>" +
    '<div class="food-meta">' + renderRatingPill(f.rating) + '<span class="spacer"></span>' + acts +
    '<span class="badge-count">💬 ' + f.comment_count + "</span>" +
    (f.image_count > 1 ? '<span class="badge-count">🖼 ' + f.image_count + "</span>" : "") +
    "</div>" +
    (f.address_text ? '<div class="food-meta" style="margin-top:-2px">📍 ' + escapeHtml(f.address_text) + "</div>" : "") +
    '<div class="food-meta" style="margin-top:-2px"><span>🕒 ' + escapeHtml(f.visited_at) +
    "</span><span class=\"spacer\"></span><span>" + escapeHtml(f.author) + "</span></div>" +
    "</div></div>";
}

function bindFoodCards(container, onFavChange, onWantChange) {
  container.querySelectorAll(".food-card").forEach(function (el) {
    el.addEventListener("click", function () {
      location.href = "/food.html?id=" + el.getAttribute("data-id");
    });
    el.querySelectorAll(".card-act").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var act = btn.dataset.act;
        if (act === "login") { location.href = "/login.html"; return; }
        var path;
        if (act === "like") path = "/api/foods/" + btn.dataset.id + "/like";
        else if (act === "want") path = "/api/foods/" + btn.dataset.id + "/want";
        else path = "/api/foods/" + btn.dataset.id + "/favorite";
        api(path, { method: "POST" }).then(function (d) {
          if (act === "like") {
            btn.classList.toggle("liked", d.liked);
            btn.innerHTML = (d.liked ? "♥" : "♡") + " " + d.like_count;
          } else if (act === "want") {
            btn.classList.toggle("wanted", d.wanted);
            btn.innerHTML = (d.wanted ? "✓ 待吃" : "＋ 待吃") + (d.want_count > 0 ? "·" + d.want_count : "");
            toast(d.wanted ? "已加入我的待吃" : "已从待吃移除");
            if (onWantChange) onWantChange(parseInt(btn.dataset.id, 10), d.wanted);
          } else {
            btn.classList.toggle("faved", d.favorited);
            btn.innerHTML = d.favorited ? "⭐" : "☆";
            toast(d.favorited ? "已加入收藏" : "已取消收藏");
            if (onFavChange) onFavChange(parseInt(btn.dataset.id, 10), d.favorited);
          }
        }).catch(function (err) { toast(err.message); });
      });
    });
  });
}

/* ---------- amap search link ---------- */
function amapSearchLink(addr) {
  return "https://uri.amap.com/search?keyword=" + encodeURIComponent(addr) + "&src=zyxqdje&callnative=1";
}

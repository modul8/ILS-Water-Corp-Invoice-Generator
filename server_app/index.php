<?php
session_start();
$config_path = __DIR__ . "/config.php";
$cfg = file_exists($config_path) ? require $config_path : [];
$api_key = $cfg["api_key"] ?? "";
$ui_password = $cfg["ui_password"] ?? "";

if ($ui_password !== "") {
    if (isset($_POST["logout"])) {
        $_SESSION["ui_authed"] = false;
    }
    if (!($_SESSION["ui_authed"] ?? false)) {
        $error = "";
        if (isset($_POST["password"])) {
            if (hash_equals($ui_password, (string)$_POST["password"])) {
                $_SESSION["ui_authed"] = true;
                header("Location: " . $_SERVER["REQUEST_URI"]);
                exit;
            }
            $error = "Invalid password";
        }
        ?>
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>Field Jobs - Login</title>
          <style>
            body { font-family: Arial, sans-serif; margin: 0; background: #111; color: #fff; display:flex; align-items:center; justify-content:center; height:100vh; }
            .card { background:#1e1e1e; padding:24px; border-radius:12px; width:320px; box-shadow:0 2px 10px rgba(0,0,0,.4); }
            input { width:100%; padding:10px; border-radius:8px; border:1px solid #444; background:#111; color:#fff; margin-top:10px; }
            button { width:100%; margin-top:12px; padding:10px; border:none; border-radius:8px; background:#4c8bf5; color:#fff; font-weight:600; }
            .error { color:#ff6b6b; margin-top:10px; }
          </style>
        </head>
        <body>
          <form class="card" method="post">
            <div style="font-size:18px; font-weight:700;">Field Jobs</div>
            <div style="color:#aaa; margin-top:6px;">Enter password</div>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Sign In</button>
            <?php if ($error): ?><div class="error"><?php echo htmlspecialchars($error); ?></div><?php endif; ?>
          </form>
        </body>
        </html>
        <?php
        exit;
    }
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Field Jobs</title>
  <link rel="icon" href="assets/ILS_WC.ico" type="image/x-icon">
  <link rel="icon" href="assets/ILS_WC.png" type="image/png">
  <link rel="apple-touch-icon" href="assets/ILS_WC.png">
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #f5f6f8; color: #222; }
    header { background: #111; color: #fff; padding: 14px 16px; font-weight: 700; }
    .wrap { max-width: 880px; margin: 0 auto; padding: 16px; }
    .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px; }
    .controls input, .controls select { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
    .card { background: #fff; border-radius: 10px; padding: 12px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.08); }
    .title { font-weight: 700; margin-bottom: 4px; font-size: 18px; }
    .title-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .map-link img { width: 66px; height: 66px; display: block; }
    .meta { color: #666; font-size: 13px; margin-bottom: 8px; }
    .module { color: #7a7a7a; font-size: 12px; font-weight: 600; letter-spacing: 0.2px; text-transform: uppercase; }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .row input { width: 120px; padding: 8px; border: 1px solid #ccc; border-radius: 6px; }
    button { background: #4c8bf5; color: #fff; border: none; padding: 10px 12px; border-radius: 8px; font-weight: 600; }
    button.secondary { background: #888; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #e2e8f0; font-size: 12px; }
  </style>
</head>
<body>
  <header>Field Jobs</header>
  <div class="wrap">
    <?php if ($ui_password !== ""): ?>
      <form method="post" style="text-align:right; margin-bottom:8px;">
        <button class="secondary" name="logout" value="1" type="submit">Sign Out</button>
      </form>
    <?php endif; ?>
    <div class="controls">
      <input id="search" placeholder="Search item / WO / PO">
      <select id="module">
        <option value="current" selected>Current Work</option>
        <option value="">All modules</option>
        <option value="drain">Drain Spraying</option>
        <option value="weeds">Noxious Weeds</option>
        <option value="tracks">Mtn Access Tracks</option>
        <option value="fire">Fire Zones</option>
      </select>
    </div>
    <div class="controls">
      <select id="completed">
        <option value="0">Not completed</option>
        <option value="1">Completed</option>
      </select>
      <button onclick="loadJobs()">Refresh</button>
    </div>
    <div id="error" class="card" style="display:none; background:#ffe4e6; color:#9f1239;"></div>
    <div id="list"></div>
  </div>

<script>
const API_KEY = <?php echo json_encode($api_key); ?>;
const API_URL = "api/index.php";

function jobTypeLabel(module) {
  if (module === "drain") return "Spray Drains";
  if (module === "weeds") return "Noxious Weeds";
  if (module === "tracks") return "Mtn Access Tracks";
  if (module === "fire") return "Fire Zones";
  return module || "Work Item";
}

function unitSuffix(unit) {
  if (unit === "km") return "km";
  if (unit === "hour") return "hr";
  return "ea";
}

function mapLink(label, lat, lon) {
  if (lat === null || lon === null || lat === "" || lon === "") return "";
  const q = encodeURIComponent(`${label || ""} @${lat},${lon}`);
  const url = `https://maps.google.com/?q=${q}`;
  return `<a class="map-link" href="${url}" target="_blank" rel="noopener" title="Open Map"><img src="assets/gps.png" alt="Map"></a>`;
}

async function apiGet(params) {
  const url = new URL(API_URL, window.location.href);
  url.searchParams.set("key", API_KEY);
  Object.keys(params).forEach(k => url.searchParams.set(k, params[k]));
  try {
    const res = await fetch(url, { headers: { "X-API-KEY": API_KEY }});
    if (!res.ok) {
      return { ok: false, error: `http_${res.status}` };
    }
    return await res.json();
  } catch (err) {
    return { ok: false, error: "network_error" };
  }
}

async function apiPost(action, body) {
  const url = new URL(API_URL, window.location.href);
  url.searchParams.set("action", action);
  url.searchParams.set("key", API_KEY);
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "X-API-KEY": API_KEY, "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    if (!res.ok) {
      return { ok: false, error: `http_${res.status}` };
    }
    return await res.json();
  } catch (err) {
    return { ok: false, error: "network_error" };
  }
}

function showError(msg) {
  const el = document.getElementById("error");
  if (!el) return;
  if (!msg) {
    el.style.display = "none";
    el.textContent = "";
    return;
  }
  el.textContent = msg;
  el.style.display = "block";
}

async function loadJobs() {
  const q = document.getElementById("search").value.trim();
  const module = document.getElementById("module").value;
  const completed = document.getElementById("completed").value;
  const isCurrent = module === "current";
  const moduleParam = isCurrent ? "" : module;
  const data = await apiGet({ action: "list", q, module: moduleParam, completed, limit: "500" });
  if (!data.ok) {
    showError("API error loading jobs. Check server connection or API key.");
    return;
  }
  showError("");
  const list = document.getElementById("list");
  list.innerHTML = "";
  if (!data.ok || !data.jobs || data.jobs.length === 0) {
    list.innerHTML = "<div class='card'>No jobs found.</div>";
    return;
  }
  let jobs = data.jobs;
  if (isCurrent) {
    jobs = jobs.filter(j => Number(j.current_work || 0) === 1);
  }
  if (jobs.length === 0) {
    list.innerHTML = "<div class='card'>No jobs found.</div>";
    return;
  }
    jobs.forEach(j => {
      let qtyVal = j.qty || j.qty_default || "";
      const unit = (j.unit || "").toLowerCase();
      const suffix = unitSuffix(unit);
      const isKm = unit === "km";
      const isDrain = (j.module || "").toLowerCase() === "drain";
      const isCompleted = Number(j.completed || 0) === 1;
      const buttonLabel = isCompleted ? "Mark Not Completed" : "Mark Completed";
      const map = mapLink(j.item || "", j.lat, j.lon);
      const latVal = (j.lat !== null && j.lat !== undefined) ? j.lat : "";
      const lonVal = (j.lon !== null && j.lon !== undefined) ? j.lon : "";
      const title = isKm && qtyVal !== "" ? `${j.item || ""} ${qtyVal}${suffix}` : `${j.item || ""}`;
      const qtyInput = isKm
        ? `<input type="hidden" value="${qtyVal}" id="qty-${j.job_key}">`
        : `<input type="number" step="0.01" placeholder="Qty" value="${qtyVal}" id="qty-${j.job_key}" ${isDrain ? "disabled" : ""}>`;
      const html = `
      <div class="card">
        <div class="title-row">
          <div class="title">${title}${!isKm ? ` <span class="badge">${suffix}</span>` : ""}</div>
          ${map ? map : ""}
        </div>
        <div class="module">${jobTypeLabel(j.module)}</div>
        <div class="meta">WO: ${j.work_order || "-"} | PO: ${j.po || "-"}</div>
        <div class="row">
          <button onclick="markCompleted('${j.job_key}', ${isCompleted ? 0 : 1})">${buttonLabel}</button>
          ${qtyInput}
        </div>
        ${isDrain ? `
        <div class="row">
          <input type="number" step="0.000001" placeholder="Lat" value="${latVal}" id="lat-${j.job_key}">
          <input type="number" step="0.000001" placeholder="Lon" value="${lonVal}" id="lon-${j.job_key}">
          <button class="secondary" onclick="useGps('${j.job_key}')">Use GPS</button>
          <button onclick="savePin('${j.job_key}')">Save Pin</button>
        </div>` : ""}
      </div>
    `;
      list.insertAdjacentHTML("beforeend", html);
    });
}

async function markCompleted(jobKey, completed) {
  const qtyEl = document.getElementById(`qty-${jobKey}`);
  const qty = qtyEl ? qtyEl.value : "";
  const resp = await apiPost("complete", { job_key: jobKey, completed: completed, qty: qty });
  if (!resp.ok) {
    showError("API error updating job. Check server connection or API key.");
  } else {
    showError("");
    loadJobs();
  }
}

function useGps(jobKey) {
  if (!navigator.geolocation) {
    alert("Geolocation is not supported on this device.");
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const latEl = document.getElementById(`lat-${jobKey}`);
      const lonEl = document.getElementById(`lon-${jobKey}`);
      if (latEl) latEl.value = pos.coords.latitude.toFixed(6);
      if (lonEl) lonEl.value = pos.coords.longitude.toFixed(6);
    },
    () => alert("Unable to get location. Check permissions.")
  );
}

async function savePin(jobKey) {
  const latEl = document.getElementById(`lat-${jobKey}`);
  const lonEl = document.getElementById(`lon-${jobKey}`);
  const lat = latEl ? latEl.value.trim() : "";
  const lon = lonEl ? lonEl.value.trim() : "";
  const resp = await apiPost("update_pin", { job_key: jobKey, lat: lat, lon: lon });
  if (!resp.ok) {
    showError("API error saving pin. Check server connection.");
  } else {
    showError("");
    loadJobs();
  }
}

loadJobs();
</script>
</body>
</html>

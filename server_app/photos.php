<?php
// Simple HTML gallery for a job's photos (API key required)
$config_path = __DIR__ . "/config.php";
if (!file_exists($config_path)) {
    http_response_code(500);
    echo "config.php missing";
    exit;
}
$cfg = require $config_path;

function get_api_key() {
    $headers = function_exists("getallheaders") ? getallheaders() : [];
    if (isset($headers["X-API-KEY"])) return trim($headers["X-API-KEY"]);
    if (isset($headers["x-api-key"])) return trim($headers["x-api-key"]);
    if (isset($_GET["key"])) return trim($_GET["key"]);
    return "";
}

if (get_api_key() !== ($cfg["api_key"] ?? "")) {
    http_response_code(401);
    echo "unauthorized";
    exit;
}

$job_key = isset($_GET["job_key"]) ? trim($_GET["job_key"]) : "";
if ($job_key === "") {
    http_response_code(400);
    echo "missing job_key";
    exit;
}

try {
    $db_host = (string)($cfg["db_host"] ?? "localhost");
    $db_port = (string)($cfg["db_port"] ?? "");
    if (strpos($db_host, ":") !== false && $db_port === "") {
        $parts = explode(":", $db_host, 2);
        $db_host = $parts[0];
        $db_port = $parts[1];
    }
    $dsn = "mysql:host=" . $db_host . ";dbname=" . $cfg["db_name"] . ";charset=utf8mb4";
    if ($db_port !== "") {
        $dsn .= ";port=" . $db_port;
    }
    $pdo = new PDO($dsn, $cfg["db_user"], $cfg["db_pass"], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    ]);
} catch (Exception $e) {
    http_response_code(500);
    echo "db_connect_failed";
    exit;
}

$stmt = $pdo->prepare("SELECT id, filename, created_at, lat, lon FROM photos WHERE job_key = :job_key ORDER BY created_at DESC");
$stmt->execute([":job_key" => $job_key]);
$rows = $stmt->fetchAll(PDO::FETCH_ASSOC);

$job_lat = null;
$job_lon = null;
try {
    $stmt = $pdo->prepare("SELECT lat, lon FROM jobs WHERE job_key = :job_key LIMIT 1");
    $stmt->execute([":job_key" => $job_key]);
    $job = $stmt->fetch(PDO::FETCH_ASSOC);
    if ($job) {
        $job_lat = $job["lat"] ?? null;
        $job_lon = $job["lon"] ?? null;
    }
} catch (Exception $e) {
    // ignore
}

$api_key = urlencode($cfg["api_key"] ?? "");
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Job Photos</title>
  <link rel="icon" href="assets/ILS_WC.ico">
  <link rel="shortcut icon" href="assets/ILS_WC.ico">
  <link rel="apple-touch-icon" href="assets/ILS_WC.png">
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; color: #222; }
    .meta { margin-bottom: 16px; color: #666; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 8px; background: #fff; }
    .card img { width: 100%; height: auto; border-radius: 6px; }
    .small { font-size: 12px; color: #666; }
    .actions { margin-top: 6px; display:flex; gap:8px; }
    .danger { background:#e11d48; color:#fff; border:none; padding:6px 8px; border-radius:6px; cursor:pointer; }
  </style>
</head>
<body>
  <h2>Job Photos</h2>
  <div class="meta">Job Key: <?php echo htmlspecialchars($job_key); ?></div>
  <?php if (!$rows): ?>
    <div>No photos found.</div>
  <?php else: ?>
    <div class="grid">
      <?php foreach ($rows as $r):
        $lat = $r["lat"] ?? null;
        $lon = $r["lon"] ?? null;
        if ($lat === null || $lat === "") $lat = $job_lat;
        if ($lon === null || $lon === "") $lon = $job_lon;
        $has_pin = ($lat !== null && $lat !== "" && $lon !== null && $lon !== "");
        $map_url = $has_pin ? ("https://maps.google.com/maps?q=" . urlencode($lat . "," . $lon)) : "";
      ?>
        <div class="card">
          <a href="photo.php?id=<?php echo (int)$r["id"]; ?>&key=<?php echo $api_key; ?>" target="_blank" rel="noopener">
            <img src="photo.php?id=<?php echo (int)$r["id"]; ?>&key=<?php echo $api_key; ?>" alt="">
          </a>
          <div class="small"><?php echo htmlspecialchars($r["filename"]); ?></div>
          <div class="small"><?php echo htmlspecialchars($r["created_at"]); ?></div>
          <?php if ($has_pin): ?>
            <div class="small">
              <a href="<?php echo htmlspecialchars($map_url); ?>" target="_blank" rel="noopener">📍 Map</a>
              (<?php echo htmlspecialchars($lat); ?>, <?php echo htmlspecialchars($lon); ?>)
            </div>
          <?php else: ?>
            <div class="small">📍 No GPS</div>
          <?php endif; ?>
          <div class="actions">
            <button class="danger" onclick="deletePhoto(<?php echo (int)$r['id']; ?>)">Delete</button>
          </div>
        </div>
      <?php endforeach; ?>
    </div>
  <?php endif; ?>

<script>
async function deletePhoto(id) {
  if (!confirm("Delete this photo?")) return;
  const form = new FormData();
  form.append("id", String(id));
  const url = new URL("api/index.php", window.location.href);
  url.searchParams.set("action", "delete_photo");
  url.searchParams.set("key", "<?php echo $api_key; ?>");
  const res = await fetch(url, { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    alert("Delete failed.");
    return;
  }
  location.reload();
}
</script>
</body>
</html>

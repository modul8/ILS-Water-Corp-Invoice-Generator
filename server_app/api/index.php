<?php
header("Content-Type: application/json; charset=utf-8");

$config_path = dirname(__DIR__) . "/config.php";
if (!file_exists($config_path)) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "config.php missing"]);
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
    echo json_encode(["ok" => false, "error" => "unauthorized"]);
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
    echo json_encode(["ok" => false, "error" => "db_connect_failed"]);
    exit;
}

function json_body() {
    $raw = file_get_contents("php://input");
    if (!$raw) return [];
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function form_or_json() {
    if (!empty($_POST)) {
        return $_POST;
    }
    return json_body();
}

$action = isset($_GET["action"]) ? $_GET["action"] : "";
$method = $_SERVER["REQUEST_METHOD"];

function ensure_upload_dir(): string {
    $dir = dirname(__DIR__) . "/uploads";
    if (!is_dir($dir)) {
        @mkdir($dir, 0775, true);
    }
    return $dir;
}

if ($action === "list" && $method === "GET") {
    $module = isset($_GET["module"]) ? trim($_GET["module"]) : "";
    $completed = isset($_GET["completed"]) ? trim($_GET["completed"]) : "";
    $q = isset($_GET["q"]) ? trim($_GET["q"]) : "";
    $limit = isset($_GET["limit"]) ? intval($_GET["limit"]) : 500;
    if ($limit <= 0 || $limit > 2000) $limit = 500;

    $where = [];
    $params = [];
    if ($module !== "") {
        $where[] = "module = :module";
        $params[":module"] = $module;
    }
    if ($completed !== "") {
        $where[] = "completed = :completed";
        $params[":completed"] = ($completed === "1" || $completed === "true") ? 1 : 0;
    }
    if ($q !== "") {
        $where[] = "(item LIKE :q OR work_order LIKE :q OR po LIKE :q)";
        $params[":q"] = "%" . $q . "%";
    }

    $sql = "SELECT job_key, module, job_type, sheet, item, lat, lon, work_order, po, unit, qty_default, completed, completed_at, invoiced, invoiced_at, qty, current_work, meta, updated_at
            FROM jobs";
    if ($where) {
        $sql .= " WHERE " . implode(" AND ", $where);
    }
    $sql .= " ORDER BY updated_at DESC LIMIT " . $limit;

    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    echo json_encode(["ok" => true, "jobs" => $rows]);
    exit;
}

if ($action === "sync_jobs" && $method === "POST") {
    $body = form_or_json();
    $jobs = isset($body["jobs"]) && is_array($body["jobs"]) ? $body["jobs"] : [];
    if (isset($body["jobs_json"]) && is_string($body["jobs_json"])) {
        $decoded = json_decode($body["jobs_json"], true);
        if (is_array($decoded)) $jobs = $decoded;
    }
    if (!$jobs) {
        echo json_encode(["ok" => true, "count" => 0]);
        exit;
    }

    $sql = "INSERT INTO jobs
        (job_key, module, job_type, sheet, item, lat, lon, work_order, po, unit, qty_default, completed, completed_at, invoiced, invoiced_at, qty, current_work, meta)
        VALUES
        (:job_key, :module, :job_type, :sheet, :item, :lat, :lon, :work_order, :po, :unit, :qty_default, :completed, :completed_at, :invoiced, :invoiced_at, :qty, :current_work, :meta)
        ON DUPLICATE KEY UPDATE
        module = VALUES(module),
        job_type = VALUES(job_type),
        sheet = VALUES(sheet),
        item = VALUES(item),
        lat = VALUES(lat),
        lon = VALUES(lon),
        work_order = VALUES(work_order),
        po = VALUES(po),
        unit = VALUES(unit),
        qty_default = VALUES(qty_default),
        completed = VALUES(completed),
        completed_at = VALUES(completed_at),
        invoiced = VALUES(invoiced),
        invoiced_at = VALUES(invoiced_at),
        qty = VALUES(qty),
        current_work = VALUES(current_work),
        meta = VALUES(meta)";

    $stmt = $pdo->prepare($sql);
    $count = 0;
    foreach ($jobs as $j) {
        if (!isset($j["job_key"]) || !isset($j["module"])) continue;
        $stmt->execute([
            ":job_key" => $j["job_key"],
            ":module" => $j["module"],
            ":job_type" => $j["job_type"] ?? "",
            ":sheet" => $j["sheet"] ?? "",
            ":item" => $j["item"] ?? "",
            ":lat" => $j["lat"] ?? null,
            ":lon" => $j["lon"] ?? null,
            ":work_order" => $j["work_order"] ?? "",
            ":po" => $j["po"] ?? "",
            ":unit" => $j["unit"] ?? "",
            ":qty_default" => isset($j["qty_default"]) ? $j["qty_default"] : null,
            ":completed" => isset($j["completed"]) ? (int)$j["completed"] : 0,
            ":completed_at" => $j["completed_at"] ?? null,
            ":invoiced" => isset($j["invoiced"]) ? (int)$j["invoiced"] : 0,
            ":invoiced_at" => $j["invoiced_at"] ?? null,
            ":qty" => isset($j["qty"]) ? $j["qty"] : null,
            ":current_work" => isset($j["current_work"]) ? (int)$j["current_work"] : 0,
            ":meta" => isset($j["meta"]) ? json_encode($j["meta"]) : null,
        ]);
        $count++;
    }
    echo json_encode(["ok" => true, "count" => $count]);
    exit;
}

if ($action === "upload" && $method === "POST") {
    if (!isset($_FILES["file"])) {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_file"]);
        exit;
    }
    $kind = isset($_POST["kind"]) ? trim($_POST["kind"]) : "file";
    $f = $_FILES["file"];
    if (!isset($f["tmp_name"]) || !is_uploaded_file($f["tmp_name"])) {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "invalid_upload"]);
        exit;
    }
    $orig = isset($f["name"]) ? basename($f["name"]) : "upload.xlsx";
    $ext = pathinfo($orig, PATHINFO_EXTENSION);
    $ts = date("Ymd_His");
    $safe_kind = preg_replace("/[^a-z0-9_-]/i", "_", $kind);
    $safe_name = preg_replace("/[^a-z0-9_.-]/i", "_", $orig);
    $fname = $safe_kind . "_" . $ts . "_" . $safe_name;
    if ($ext) {
        // keep extension from original name
    } else {
        $fname .= ".xlsx";
    }
    $dir = ensure_upload_dir();
    $dest = $dir . "/" . $fname;
    if (!move_uploaded_file($f["tmp_name"], $dest)) {
        http_response_code(500);
        echo json_encode(["ok" => false, "error" => "save_failed"]);
        exit;
    }
    echo json_encode([
        "ok" => true,
        "filename" => $fname,
        "kind" => $safe_kind,
        "size" => isset($f["size"]) ? (int)$f["size"] : null,
    ]);
    exit;
}

if ($action === "upsert" && $method === "GET") {
    $job_key = isset($_GET["job_key"]) ? trim($_GET["job_key"]) : "";
    $module = isset($_GET["module"]) ? trim($_GET["module"]) : "";
    if ($job_key === "" || $module === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing job_key/module"]);
        exit;
    }

    $payload = [
        ":job_key" => $job_key,
        ":module" => $module,
        ":job_type" => $_GET["job_type"] ?? "",
        ":sheet" => $_GET["sheet"] ?? "",
        ":item" => $_GET["item"] ?? "",
        ":lat" => $_GET["lat"] ?? null,
        ":lon" => $_GET["lon"] ?? null,
        ":work_order" => $_GET["work_order"] ?? "",
        ":po" => $_GET["po"] ?? "",
        ":unit" => $_GET["unit"] ?? "",
        ":qty_default" => isset($_GET["qty_default"]) ? $_GET["qty_default"] : null,
        ":completed" => isset($_GET["completed"]) ? (int)$_GET["completed"] : 0,
        ":completed_at" => $_GET["completed_at"] ?? null,
        ":invoiced" => isset($_GET["invoiced"]) ? (int)$_GET["invoiced"] : 0,
        ":invoiced_at" => $_GET["invoiced_at"] ?? null,
        ":qty" => isset($_GET["qty"]) ? $_GET["qty"] : null,
        ":current_work" => isset($_GET["current_work"]) ? (int)$_GET["current_work"] : 0,
        ":meta" => null,
    ];

    $sql = "INSERT INTO jobs
        (job_key, module, job_type, sheet, item, lat, lon, work_order, po, unit, qty_default, completed, completed_at, invoiced, invoiced_at, qty, current_work, meta)
        VALUES
        (:job_key, :module, :job_type, :sheet, :item, :lat, :lon, :work_order, :po, :unit, :qty_default, :completed, :completed_at, :invoiced, :invoiced_at, :qty, :current_work, :meta)
        ON DUPLICATE KEY UPDATE
        module = VALUES(module),
        job_type = VALUES(job_type),
        sheet = VALUES(sheet),
        item = VALUES(item),
        lat = VALUES(lat),
        lon = VALUES(lon),
        work_order = VALUES(work_order),
        po = VALUES(po),
        unit = VALUES(unit),
        qty_default = VALUES(qty_default),
        completed = VALUES(completed),
        completed_at = VALUES(completed_at),
        invoiced = VALUES(invoiced),
        invoiced_at = VALUES(invoiced_at),
        qty = VALUES(qty),
        current_work = VALUES(current_work)";

    $stmt = $pdo->prepare($sql);
    $stmt->execute($payload);
    echo json_encode(["ok" => true]);
    exit;
}

if ($action === "complete" && $method === "POST") {
    $body = form_or_json();
    $job_key = isset($body["job_key"]) ? trim($body["job_key"]) : "";
    if ($job_key === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing job_key"]);
        exit;
    }
    $completed = isset($body["completed"]) ? (int)!!$body["completed"] : 1;
    $qty = isset($body["qty"]) ? $body["qty"] : null;
    $completed_at = isset($body["completed_at"]) ? $body["completed_at"] : date("Y-m-d");
    $module = "";
    $stmt = $pdo->prepare("SELECT module FROM jobs WHERE job_key = :job_key");
    $stmt->execute([":job_key" => $job_key]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if ($row && isset($row["module"])) {
        $module = (string)$row["module"];
    }

    if ($module === "drain") {
        $sql = "UPDATE jobs SET completed = :completed, completed_at = :completed_at WHERE job_key = :job_key";
        $stmt = $pdo->prepare($sql);
        $stmt->execute([
            ":completed" => $completed,
            ":completed_at" => $completed ? $completed_at : null,
            ":job_key" => $job_key,
        ]);
    } else {
        $sql = "UPDATE jobs SET completed = :completed, completed_at = :completed_at, qty = :qty WHERE job_key = :job_key";
        $stmt = $pdo->prepare($sql);
        $stmt->execute([
            ":completed" => $completed,
            ":completed_at" => $completed ? $completed_at : null,
            ":qty" => $qty,
            ":job_key" => $job_key,
        ]);
    }
    echo json_encode(["ok" => true]);
    exit;
}

if ($action === "complete" && $method === "GET") {
    $job_key = isset($_GET["job_key"]) ? trim($_GET["job_key"]) : "";
    if ($job_key === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing job_key"]);
        exit;
    }
    $completed = isset($_GET["completed"]) ? (int)$_GET["completed"] : 1;
    $qty = isset($_GET["qty"]) ? $_GET["qty"] : null;
    $completed_at = isset($_GET["completed_at"]) ? $_GET["completed_at"] : date("Y-m-d");
    $module = "";
    $stmt = $pdo->prepare("SELECT module FROM jobs WHERE job_key = :job_key");
    $stmt->execute([":job_key" => $job_key]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if ($row && isset($row["module"])) {
        $module = (string)$row["module"];
    }

    if ($module === "drain") {
        $sql = "UPDATE jobs SET completed = :completed, completed_at = :completed_at WHERE job_key = :job_key";
        $stmt = $pdo->prepare($sql);
        $stmt->execute([
            ":completed" => $completed,
            ":completed_at" => $completed ? $completed_at : null,
            ":job_key" => $job_key,
        ]);
    } else {
        $sql = "UPDATE jobs SET completed = :completed, completed_at = :completed_at, qty = :qty WHERE job_key = :job_key";
        $stmt = $pdo->prepare($sql);
        $stmt->execute([
            ":completed" => $completed,
            ":completed_at" => $completed ? $completed_at : null,
            ":qty" => $qty,
            ":job_key" => $job_key,
        ]);
    }
    echo json_encode(["ok" => true]);
    exit;
}

if ($action === "set_invoiced" && $method === "GET") {
    $job_key = isset($_GET["job_key"]) ? trim($_GET["job_key"]) : "";
    if ($job_key === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing job_key"]);
        exit;
    }
    $invoiced = isset($_GET["invoiced"]) ? (int)$_GET["invoiced"] : 1;
    $invoiced_at = isset($_GET["invoiced_at"]) ? $_GET["invoiced_at"] : date("Y-m-d");

    $sql = "UPDATE jobs SET invoiced = :invoiced, invoiced_at = :invoiced_at WHERE job_key = :job_key";
    $stmt = $pdo->prepare($sql);
    $stmt->execute([
        ":invoiced" => $invoiced,
        ":invoiced_at" => $invoiced ? $invoiced_at : null,
        ":job_key" => $job_key,
    ]);
    echo json_encode(["ok" => true]);
    exit;
}

if ($action === "changes" && $method === "GET") {
    $since = isset($_GET["since"]) ? trim($_GET["since"]) : "";
    if ($since === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing since"]);
        exit;
    }
    $sql = "SELECT job_key, module, completed, completed_at, qty, updated_at
            FROM jobs WHERE updated_at >= :since ORDER BY updated_at ASC";
    $stmt = $pdo->prepare($sql);
    $stmt->execute([":since" => $since]);
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    echo json_encode(["ok" => true, "jobs" => $rows]);
    exit;
}

http_response_code(404);
echo json_encode(["ok" => false, "error" => "unknown action"]);

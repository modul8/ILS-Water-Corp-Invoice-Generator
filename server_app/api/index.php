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

function debug_enabled(array $cfg): bool {
    if (isset($_GET["debug"]) && ($_GET["debug"] === "1" || $_GET["debug"] === "true")) {
        return true;
    }
    return (bool)($cfg["debug"] ?? false);
}

function norm_date($value) {
    if ($value === null) return null;
    if (is_string($value) && trim($value) === "") return null;
    return $value;
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

function latest_upload(string $prefix): string {
    $dir = ensure_upload_dir();
    $files = glob($dir . "/" . $prefix . "*.xlsx");
    if (!$files) return "";
    rsort($files, SORT_STRING);
    return $files[0];
}

function master_spray_list(array $cfg): string {
    $path = trim((string)($cfg["spray_list_master"] ?? ""));
    if ($path !== "" && file_exists($path)) return $path;
    if ($path !== "" && !file_exists($path)) {
        $latest = latest_upload("spray_list_");
        if ($latest !== "") return $latest;
    }
    return latest_upload("spray_list_");
}

function module_sheet_name(string $module): string {
    $m = strtolower(trim($module));
    if ($m === "drain") return "Spray Drains";
    if ($m === "weeds") return "Noxious Weeds";
    if ($m === "tracks") return "Mtn Access Tracks";
    if ($m === "fire") return "Fire Zones";
    return $module !== "" ? $module : "Work Items";
}

function ensure_sheet_with_headers($spreadsheet, string $sheetName, array $headers) {
    $ws = $spreadsheet->getSheetByName($sheetName);
    if ($ws === null) {
        $ws = new \PhpOffice\PhpSpreadsheet\Worksheet\Worksheet($spreadsheet, $sheetName);
        $spreadsheet->addSheet($ws);
    }
    $a1 = trim((string)$ws->getCell("A1")->getValue());
    if ($a1 === "") {
        $col = 1;
        foreach ($headers as $h) {
            $cell = \PhpOffice\PhpSpreadsheet\Cell\Coordinate::stringFromColumnIndex($col) . "1";
            $ws->setCellValue($cell, $h);
            $col++;
        }
    }
    return $ws;
}

function update_spray_pin(string $path, string $sheet, string $drain, $lat, $lon): array {
    if (!file_exists($path)) {
        return ["ok" => false, "error" => "spray_list_not_found"];
    }
    if (!file_exists(dirname(__DIR__) . "/vendor/autoload.php")) {
        return ["ok" => false, "error" => "phpspreadsheet_missing"];
    }
    require_once dirname(__DIR__) . "/vendor/autoload.php";
    try {
        $spreadsheet = \PhpOffice\PhpSpreadsheet\IOFactory::load($path);
    } catch (Exception $e) {
        return ["ok" => false, "error" => "spray_list_load_failed", "detail" => $e->getMessage()];
    }

    $ws = $spreadsheet->getSheetByName($sheet);
    if ($ws === null) {
        return ["ok" => false, "error" => "sheet_not_found"];
    }

    $header_row = 0;
    for ($r = 1; $r <= 20; $r++) {
        $a = trim((string)$ws->getCell("A{$r}")->getValue());
        $d = trim((string)$ws->getCell("D{$r}")->getValue());
        if ($a !== "" && stripos($a, "DRAIN") !== false && stripos($d, "TOTAL") !== false) {
            $header_row = $r;
            break;
        }
    }
    $start_row = $header_row ? $header_row + 1 : 1;
    $end_row = $ws->getHighestRow();
    $found = false;
    for ($r = $start_row; $r <= $end_row; $r++) {
        $name = trim((string)$ws->getCell("A{$r}")->getValue());
        if ($name === "") {
            continue;
        }
        if (strcasecmp($name, $drain) === 0) {
            $ws->setCellValue("F{$r}", $lat === "" ? null : $lat);
            $ws->setCellValue("G{$r}", $lon === "" ? null : $lon);
            $found = true;
            break;
        }
    }
    if (!$found) {
        return ["ok" => false, "error" => "drain_not_found_in_sheet"];
    }

    try {
        $writer = \PhpOffice\PhpSpreadsheet\IOFactory::createWriter($spreadsheet, "Xlsx");
        $writer->save($path);
    } catch (Exception $e) {
        return ["ok" => false, "error" => "spray_list_save_failed", "detail" => $e->getMessage()];
    }

    return ["ok" => true];
}

function update_module_pin(string $path, string $sheet, array $job): array {
    if (!file_exists($path)) {
        return ["ok" => false, "error" => "spray_list_not_found"];
    }
    if (!file_exists(dirname(__DIR__) . "/vendor/autoload.php")) {
        return ["ok" => false, "error" => "phpspreadsheet_missing"];
    }
    require_once dirname(__DIR__) . "/vendor/autoload.php";

    try {
        $spreadsheet = \PhpOffice\PhpSpreadsheet\IOFactory::load($path);
    } catch (Exception $e) {
        return ["ok" => false, "error" => "spray_list_load_failed", "detail" => $e->getMessage()];
    }

    $headers = [
        "job_key","module","job_type","sheet","item","lat","lon","work_order","po","unit",
        "qty_default","completed","completed_at","invoiced","invoiced_at","qty","current_work","meta","updated_at"
    ];
    $ws = ensure_sheet_with_headers($spreadsheet, $sheet, $headers);

    $jobKey = (string)($job["job_key"] ?? "");
    if ($jobKey === "") {
        return ["ok" => false, "error" => "missing_job_key"];
    }

    $rowData = [
        "job_key" => $jobKey,
        "module" => (string)($job["module"] ?? ""),
        "job_type" => (string)($job["job_type"] ?? ""),
        "sheet" => (string)($job["sheet"] ?? ""),
        "item" => (string)($job["item"] ?? ""),
        "lat" => $job["lat"] ?? null,
        "lon" => $job["lon"] ?? null,
        "work_order" => (string)($job["work_order"] ?? ""),
        "po" => (string)($job["po"] ?? ""),
        "unit" => (string)($job["unit"] ?? ""),
        "qty_default" => $job["qty_default"] ?? null,
        "completed" => $job["completed"] ?? null,
        "completed_at" => $job["completed_at"] ?? null,
        "invoiced" => $job["invoiced"] ?? null,
        "invoiced_at" => $job["invoiced_at"] ?? null,
        "qty" => $job["qty"] ?? null,
        "current_work" => $job["current_work"] ?? null,
        "meta" => $job["meta"] ?? null,
        "updated_at" => $job["updated_at"] ?? null,
    ];

    $endRow = $ws->getHighestRow();
    $targetRow = 0;
    for ($r = 2; $r <= $endRow; $r++) {
        $val = trim((string)$ws->getCell("A{$r}")->getValue());
        if ($val === $jobKey) {
            $targetRow = $r;
            break;
        }
    }
    if ($targetRow === 0) {
        $targetRow = $endRow + 1;
    }

    $col = 1;
    foreach ($headers as $h) {
        $cell = \PhpOffice\PhpSpreadsheet\Cell\Coordinate::stringFromColumnIndex($col) . (string)$targetRow;
        $ws->setCellValue($cell, $rowData[$h]);
        $col++;
    }

    try {
        $writer = \PhpOffice\PhpSpreadsheet\IOFactory::createWriter($spreadsheet, "Xlsx");
        $writer->save($path);
    } catch (Exception $e) {
        return ["ok" => false, "error" => "spray_list_save_failed", "detail" => $e->getMessage()];
    }

    return ["ok" => true];
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

if ($action === "search_item" && $method === "GET") {
    $q = isset($_GET["q"]) ? trim($_GET["q"]) : "";
    $module = isset($_GET["module"]) ? trim($_GET["module"]) : "";
    $limit = isset($_GET["limit"]) ? intval($_GET["limit"]) : 20;
    if ($limit <= 0 || $limit > 200) $limit = 20;
    if ($q === "") {
        echo json_encode(["ok" => true, "jobs" => []]);
        exit;
    }

    $sql = "SELECT job_key, module, job_type, sheet, item, lat, lon, work_order, po, unit, qty_default, completed, completed_at, invoiced, invoiced_at, qty, current_work, meta, updated_at
            FROM jobs
            WHERE item LIKE :q";
    $params = [":q" => "%" . $q . "%"];
    if ($module !== "") {
        $sql .= " AND module = :module";
        $params[":module"] = $module;
    }
    $sql .= " ORDER BY updated_at DESC LIMIT " . $limit;

    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    echo json_encode(["ok" => true, "jobs" => $rows]);
    exit;
}

if ($action === "update_pin" && $method === "POST") {
    $body = form_or_json();
    $job_key = isset($body["job_key"]) ? trim($body["job_key"]) : "";
    $lat = isset($body["lat"]) ? trim((string)$body["lat"]) : "";
    $lon = isset($body["lon"]) ? trim((string)$body["lon"]) : "";
    if ($job_key === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_job_key"]);
        exit;
    }

    $stmt = $pdo->prepare("UPDATE jobs SET lat = :lat, lon = :lon WHERE job_key = :job_key");
    $stmt->execute([
        ":lat" => $lat === "" ? null : $lat,
        ":lon" => $lon === "" ? null : $lon,
        ":job_key" => $job_key,
    ]);

    $stmt = $pdo->prepare("SELECT job_key, module, job_type, sheet, item, lat, lon, work_order, po, unit, qty_default, completed, completed_at, invoiced, invoiced_at, qty, current_work, meta, updated_at FROM jobs WHERE job_key = :job_key");
    $stmt->execute([":job_key" => $job_key]);
    $job = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!$job) {
        http_response_code(500);
        echo json_encode(["ok" => false, "error" => "job_not_found"]);
        exit;
    }

    $parts = explode("|", $job_key, 3);
    $sheet = isset($body["sheet"]) && $body["sheet"] !== "" ? trim((string)$body["sheet"]) : ($parts[0] ?? "");
    $drain = isset($body["drain"]) && $body["drain"] !== "" ? trim((string)$body["drain"]) : ($parts[2] ?? "");

    $path = master_spray_list($cfg);
    $debug = debug_enabled($cfg);
    try {
        if (strtolower((string)($job["module"] ?? "")) === "drain") {
            if ($sheet === "" || $drain === "") {
                http_response_code(400);
                echo json_encode(["ok" => false, "error" => "missing_sheet_or_drain"]);
                exit;
            }
            $pin_result = update_spray_pin($path, $sheet, $drain, $lat, $lon);
        } else {
            $pin_result = update_module_pin($path, module_sheet_name((string)($job["module"] ?? "")), $job);
        }
    } catch (Exception $e) {
        http_response_code(500);
        $resp = ["ok" => false, "error" => "pin_update_exception"];
        if ($debug) {
            $resp["detail"] = $e->getMessage();
            $resp["path"] = $path;
        }
        echo json_encode($resp);
        exit;
    } catch (Error $e) {
        http_response_code(500);
        $resp = ["ok" => false, "error" => "pin_update_error"];
        if ($debug) {
            $resp["detail"] = $e->getMessage();
            $resp["path"] = $path;
        }
        echo json_encode($resp);
        exit;
    }
    if (!$pin_result["ok"]) {
        http_response_code(500);
        if ($debug) {
            $pin_result["path"] = $path;
        }
        echo json_encode($pin_result);
        exit;
    }

    echo json_encode(["ok" => true]);
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
    try {
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
                ":completed_at" => norm_date($j["completed_at"] ?? null),
                ":invoiced" => isset($j["invoiced"]) ? (int)$j["invoiced"] : 0,
                ":invoiced_at" => norm_date($j["invoiced_at"] ?? null),
                ":qty" => isset($j["qty"]) ? $j["qty"] : null,
                ":current_work" => isset($j["current_work"]) ? (int)$j["current_work"] : 0,
                ":meta" => isset($j["meta"]) ? json_encode($j["meta"]) : null,
            ]);
            $count++;
        }
    } catch (Exception $e) {
        http_response_code(500);
        error_log("sync_jobs failed: " . $e->getMessage());
        echo json_encode(["ok" => false, "error" => $e->getMessage()]);
        exit;
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
        ":completed_at" => norm_date($_GET["completed_at"] ?? null),
        ":invoiced" => isset($_GET["invoiced"]) ? (int)$_GET["invoiced"] : 0,
        ":invoiced_at" => norm_date($_GET["invoiced_at"] ?? null),
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
    try {
        $stmt->execute($payload);
    } catch (Exception $e) {
        http_response_code(500);
        error_log("upsert failed: " . $e->getMessage());
        echo json_encode(["ok" => false, "error" => $e->getMessage()]);
        exit;
    }
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

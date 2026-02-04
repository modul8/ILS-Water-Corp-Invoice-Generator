<?php
header("Content-Type: application/json; charset=utf-8");

$config_path = dirname(__DIR__) . "/config.php";
if (!file_exists($config_path)) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "config.php missing"]);
    exit;
}
$uploads_dir = dirname(__DIR__) . "/uploads";
if (!is_dir($uploads_dir)) {
    @mkdir($uploads_dir, 0775, true);
}
@ini_set("log_errors", "1");
@ini_set("error_log", $uploads_dir . "/php_errors.log");
register_shutdown_function(function () use ($uploads_dir) {
    $err = error_get_last();
    if (!$err) return;
    if (!isset($err["type"]) || !in_array($err["type"], [E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR], true)) return;
    $line = json_encode([
        "ts" => date("Y-m-d H:i:s"),
        "type" => $err["type"],
        "message" => $err["message"] ?? "",
        "file" => $err["file"] ?? "",
        "line" => $err["line"] ?? "",
    ], JSON_UNESCAPED_SLASHES);
    if ($line) {
        @file_put_contents($uploads_dir . "/php_errors.log", $line . "\n", FILE_APPEND | LOCK_EX);
    }
});
$cfg = require $config_path;
$GLOBALS["CFG"] = $cfg;

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

try {
    $pdo->exec("CREATE TABLE IF NOT EXISTS photos (
        id INT AUTO_INCREMENT PRIMARY KEY,
        job_key VARCHAR(255) NOT NULL,
        filename VARCHAR(255) NOT NULL,
        stored_path TEXT NOT NULL,
        lat DECIMAL(10,6) DEFAULT NULL,
        lon DECIMAL(10,6) DEFAULT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_photos_job_key (job_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
} catch (Exception $e) {
    // ignore: app can run without photos table
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

function ensure_photos_dir(string $job_key): string {
    $base = ensure_upload_dir() . "/job_photos";
    if (!is_dir($base)) {
        @mkdir($base, 0775, true);
    }
    $safe = preg_replace('/[^A-Za-z0-9._-]+/', "_", $job_key);
    $dir = $base . "/" . $safe;
    if (!is_dir($dir)) {
        @mkdir($dir, 0775, true);
    }
    return $dir;
}

function photos_base_url(array $cfg): string {
    $base = (string)($cfg["photo_base_url"] ?? "");
    if ($base !== "") return rtrim($base, "/");
    $scheme = (!empty($_SERVER["HTTPS"]) && $_SERVER["HTTPS"] !== "off") ? "https" : "http";
    $host = $_SERVER["HTTP_HOST"] ?? "";
    return $scheme . "://" . $host;
}

function norm_str($s): string {
    if ($s === null) return "";
    $s = strtoupper((string)$s);
    $s = preg_replace('/[^A-Z0-9]+/', ' ', $s);
    $s = preg_replace('/\s+/', ' ', $s);
    return trim($s);
}

function safe_float_val($x): ?float {
    if ($x === null) return null;
    if (is_int($x) || is_float($x)) return (float)$x;
    $s = trim((string)$x);
    if ($s === "") return null;
    $s = str_replace([",", " "], "", $s);
    if (preg_match('/^(\\d+)\\+(\\d+(?:\\.\\d+)?)$/', $s, $m)) {
        return ((float)$m[1] * 1000.0) + (float)$m[2];
    }
    if (!is_numeric($s)) return null;
    return (float)$s;
}

function first_token_norm($s): string {
    $t = norm_str($s);
    if ($t === "") return "";
    $parts = explode(" ", $t);
    return $parts[0] ?? "";
}

function strip_segment_suffix(string $name): string {
    if (preg_match('/^(.*)\\([0-9.]+\\s*-\\s*[0-9.]+\\)\\s*$/', $name, $m)) {
        return trim($m[1]);
    }
    return $name;
}

function cell_value($ws, int $col, int $row) {
    if (method_exists($ws, "getCellByColumnAndRow")) {
        $cell = $ws->getCellByColumnAndRow($col, $row);
    } else {
        $colStr = \PhpOffice\PhpSpreadsheet\Cell\Coordinate::stringFromColumnIndex($col);
        $cell = $ws->getCell($colStr . $row);
    }
    $v = $cell->getValue();
    if (is_string($v) && $v !== "" && $v[0] === "=") {
        try {
            $calc = $cell->getCalculatedValue();
            if ($calc !== null) return $calc;
        } catch (Exception $e) {
            // ignore formula calc errors, fall back to raw value
        }
    }
    return $v;
}

function parse_range_str($s): ?array {
    if ($s === null) return null;
    $t = trim((string)$s);
    if ($t === "") return null;
    if (preg_match('/^\\s*([0-9.+\\s]+)\\s*[-–]\\s*([0-9.+\\s]+)\\s*$/', $t, $m)) {
        $a = safe_float_val($m[1]);
        $b = safe_float_val($m[2]);
        if ($a !== null && $b !== null) return [$a, $b];
    }
    return null;
}

function spray_list_rows(string $path): array {
    $rows = [];
    if (!file_exists($path)) return $rows;
    if (!file_exists(dirname(__DIR__) . "/vendor/autoload.php")) return $rows;
    require_once dirname(__DIR__) . "/vendor/autoload.php";
    $wb = \PhpOffice\PhpSpreadsheet\IOFactory::load($path);
    foreach ($wb->getWorksheetIterator() as $ws) {
        $current_catchment = null;
        $header_row = 0;
        $max_row = $ws->getHighestRow();
        $scan_to = min($max_row, 60);
        for ($r = 1; $r <= $scan_to; $r++) {
            $a_norm = norm_str(cell_value($ws, 1, $r));
            $d_norm = norm_str(cell_value($ws, 4, $r));
            if ($a_norm === "DRAIN NAME") { $header_row = $r; break; }
            if (strpos($a_norm, "DRAIN") !== false && in_array($d_norm, ["TOTAL DIST", "TOTAL DISTANCE"], true)) {
                $header_row = $r; break;
            }
        }
        $start_row = $header_row ? $header_row + 1 : 2;
        $last_drain = "";
        for ($r = $start_row; $r <= $max_row; $r++) {
            $a = cell_value($ws, 1, $r);
            $b = cell_value($ws, 2, $r);
            $c = cell_value($ws, 3, $r);
            $d = cell_value($ws, 4, $r);
            $lat_raw = cell_value($ws, 6, $r);
            $lon_raw = cell_value($ws, 7, $r);

            $a_str = $a !== null ? trim((string)$a) : "";
            $a_norm = norm_str($a_str);
            if (
                $a_str === "" &&
                ($d === null || trim((string)$d) === "") &&
                ($b === null || trim((string)$b) === "") &&
                ($c === null || trim((string)$c) === "")
            ) {
                continue;
            }
            if ($a_str !== "" && strpos($a_norm, "CATCHMENT") !== false && ($d === null || trim((string)$d) === "")) {
                $current_catchment = $a_str;
                continue;
            }
            if (in_array($a_norm, ["DRAIN NAME", "TOTAL DIST", "TOTAL DISTANCE"], true)) continue;
            if (strpos($a_norm, "TOTAL ") === 0) continue;

            $drain = $a_str;
            if ($drain === "") {
                if ($last_drain === "") continue;
                $drain = $last_drain;
            } else {
                $last_drain = $drain;
            }

            $start_m = safe_float_val($b);
            $end_m = safe_float_val($c);
            if ($start_m === null && $end_m === null) {
                $range = parse_range_str($b);
                if ($range) {
                    $start_m = $range[0];
                    $end_m = $range[1];
                }
            }
            if ($start_m !== null && $end_m !== null) {
                $fmt = function ($x) { return (floor($x) == $x) ? (string)(int)$x : (string)$x; };
                $drain = $drain . " (" . $fmt($start_m) . "-" . $fmt($end_m) . ")";
            }

            $dist = safe_float_val($d);
            if ($dist === null && $start_m !== null && $end_m !== null && $end_m >= $start_m) {
                $dist = $end_m - $start_m;
            }
            if ($dist === null) continue;

            $rows[] = [
                "sheet" => $ws->getTitle(),
                "catchment" => $current_catchment ?? "",
                "drain" => $drain,
                "distance_m" => (float)$dist,
                "qty_km" => round(((float)$dist) / 1000.0, 2),
                "start_m" => $start_m,
                "end_m" => $end_m,
                "lat" => safe_float_val($lat_raw),
                "lon" => safe_float_val($lon_raw),
            ];
        }
    }
    // If a drain has segmented rows, drop the unsegmented base row
    $has_segment = [];
    foreach ($rows as $r) {
        if ($r["start_m"] !== null && $r["end_m"] !== null) {
            $key = norm_str($r["sheet"]) . "|" . norm_str($r["catchment"]) . "|" . norm_str(strip_segment_suffix($r["drain"]));
            $has_segment[$key] = true;
        }
    }
    if ($has_segment) {
        $rows = array_values(array_filter($rows, function ($r) use ($has_segment) {
            if ($r["start_m"] !== null && $r["end_m"] !== null) return true;
            $key = norm_str($r["sheet"]) . "|" . norm_str($r["catchment"]) . "|" . norm_str(strip_segment_suffix($r["drain"]));
            return !isset($has_segment[$key]);
        }));
    }
    return $rows;
}

function extract_spray_group(string $maint_norm): string {
    if ($maint_norm === "") return "";
    // Try regex for common patterns: "52W SPRAY DRAIN(S) X"
    if (preg_match('/\\b52W\\s+SPRAY\\s+DRAINS?\\s+([A-Z0-9-]+)\\b/', $maint_norm, $m)) {
        return $m[1];
    }
    if (preg_match('/\\bSPRAY\\s+DRAINS?\\s+([A-Z0-9-]+)\\b/', $maint_norm, $m)) {
        return $m[1];
    }
    $toks = explode(" ", $maint_norm);
    $len = count($toks);
    for ($i = 0; $i < $len - 1; $i++) {
        if ($toks[$i] === "SPRAY" && in_array($toks[$i + 1], ["DRAIN", "DRAINS"], true)) {
            for ($j = $i + 2; $j < $len; $j++) {
                if ($toks[$j] !== "" && $toks[$j] !== "DRAIN" && $toks[$j] !== "DRAINS") {
                    return $toks[$j];
                }
            }
        }
    }
    return "";
}

function work_list_mapping(string $path, string $sheet_name = ""): array {
    $po = "";
    $mapping = [];
    if (!file_exists($path)) return [$po, $mapping];
    if (!file_exists(dirname(__DIR__) . "/vendor/autoload.php")) return [$po, $mapping];
    require_once dirname(__DIR__) . "/vendor/autoload.php";
    $wb = \PhpOffice\PhpSpreadsheet\IOFactory::load($path);

    $sheets = [];
    if ($sheet_name !== "" && $wb->sheetNameExists($sheet_name)) {
        $sheets[] = $wb->getSheetByName($sheet_name);
    } else {
        foreach ($wb->getWorksheetIterator() as $ws) {
            $sheets[] = $ws;
        }
    }

    $best_mapping = [];
    $best_po = "";
    foreach ($sheets as $ws) {
        $po_raw = $ws->getCell("A1")->getValue();
        if (!$po_raw) $po_raw = $ws->getCell("B1")->getValue();
        if (!$po_raw) $po_raw = $ws->getCell("C1")->getValue();
        $sheet_po = $po_raw !== null ? trim((string)$po_raw) : "";

        $header_row = 2;
        $max_row = $ws->getHighestRow();
        $max_col_str = $ws->getHighestColumn();
        $max_col_idx = \PhpOffice\PhpSpreadsheet\Cell\Coordinate::columnIndexFromString($max_col_str);
        $max_col_idx = min(120, $max_col_idx);
        for ($r = 1; $r <= min($max_row, 80); $r++) {
            $row = [];
            for ($c = 1; $c <= $max_col_idx; $c++) {
                $row[] = norm_str(cell_value($ws, $c, $r));
            }
            if ((in_array("MI", $row, true) || in_array("MI #", $row, true)) &&
                (in_array("MAINTITEM TEXT", $row, true) || in_array("LOCATION GROUPING", $row, true))) {
                $header_row = $r;
                break;
            }
        }

        $mi_col = null;
        $maint_col = null;
        for ($c = 1; $c <= $max_col_idx; $c++) {
            $h = norm_str(cell_value($ws, $c, $header_row));
            if ($h === "MI" || $h === "MI #") $mi_col = $c;
            if ($h === "MAINTITEM TEXT") $maint_col = $c;
        }
        if ($maint_col === null) {
            for ($c = 1; $c <= $max_col_idx; $c++) {
                $h = norm_str(cell_value($ws, $c, $header_row));
                if ($h === "LOCATION GROUPING") { $maint_col = $c; break; }
            }
        }
        if ($mi_col === null) $mi_col = 2;
        if ($maint_col === null) continue;

        $sheet_mapping = [];
        for ($r = $header_row + 1; $r <= $max_row; $r++) {
            $mi = cell_value($ws, $mi_col, $r);
            $maint = cell_value($ws, $maint_col, $r);
            if (!$mi || !$maint) continue;
            $mi_s = trim((string)$mi);
            if ($mi_s === "") continue;
            $t = norm_str($maint);
            if ($t === "") continue;
            $group = extract_spray_group($t);
            if ($group === "") continue;
            $sheet_mapping[$group] = $mi_s;
        }

        if (count($sheet_mapping) > count($best_mapping)) {
            $best_mapping = $sheet_mapping;
            $best_po = $sheet_po;
        }
    }

    return [$best_po, $best_mapping];
}

function work_list_mapping_debug(string $path): array {
    $out = [
        "ok" => false,
        "path" => $path,
        "sheets" => [],
    ];
    if (!file_exists($path)) {
        $out["error"] = "work_list_not_found";
        return $out;
    }
    if (!file_exists(dirname(__DIR__) . "/vendor/autoload.php")) {
        $out["error"] = "phpspreadsheet_missing";
        return $out;
    }
    require_once dirname(__DIR__) . "/vendor/autoload.php";
    $wb = \PhpOffice\PhpSpreadsheet\IOFactory::load($path);

    foreach ($wb->getWorksheetIterator() as $ws) {
        $info = [
            "sheet" => $ws->getTitle(),
            "po" => "",
            "header_row" => null,
            "mi_col" => null,
            "maint_col" => null,
            "mapping_count" => 0,
            "samples" => [],
        ];
        $po_raw = $ws->getCell("A1")->getValue();
        if (!$po_raw) $po_raw = $ws->getCell("B1")->getValue();
        if (!$po_raw) $po_raw = $ws->getCell("C1")->getValue();
        $info["po"] = $po_raw !== null ? trim((string)$po_raw) : "";

        $header_row = 2;
        $max_row = $ws->getHighestRow();
        $max_col_str = $ws->getHighestColumn();
        $max_col_idx = \PhpOffice\PhpSpreadsheet\Cell\Coordinate::columnIndexFromString($max_col_str);
        $max_col_idx = min(120, $max_col_idx);
        for ($r = 1; $r <= min($max_row, 80); $r++) {
            $row = [];
            for ($c = 1; $c <= $max_col_idx; $c++) {
                $row[] = norm_str(cell_value($ws, $c, $r));
            }
            if ((in_array("MI", $row, true) || in_array("MI #", $row, true)) &&
                (in_array("MAINTITEM TEXT", $row, true) || in_array("LOCATION GROUPING", $row, true))) {
                $header_row = $r;
                break;
            }
        }
        $info["header_row"] = $header_row;
        $header_vals = [];
        for ($c = 1; $c <= min($max_col_idx, 40); $c++) {
            $header_vals[] = norm_str(cell_value($ws, $c, $header_row));
        }
        $info["header_values"] = $header_vals;
        $sample_rows = [];
        for ($r = 1; $r <= min($max_row, 8); $r++) {
            $vals = [];
            for ($c = 1; $c <= min($max_col_idx, 20); $c++) {
                $v = cell_value($ws, $c, $r);
                $vals[] = is_string($v) ? trim($v) : $v;
            }
            $sample_rows[] = ["row" => $r, "values" => $vals];
        }
        $info["sample_rows"] = $sample_rows;

        $mi_col = null;
        $maint_col = null;
        for ($c = 1; $c <= $max_col_idx; $c++) {
            $h = norm_str(cell_value($ws, $c, $header_row));
            if ($h === "MI" || $h === "MI #") $mi_col = $c;
            if ($h === "MAINTITEM TEXT") $maint_col = $c;
        }
        if ($maint_col === null) {
            for ($c = 1; $c <= $max_col_idx; $c++) {
                $h = norm_str(cell_value($ws, $c, $header_row));
                if ($h === "LOCATION GROUPING") { $maint_col = $c; break; }
            }
        }
        if ($mi_col === null) $mi_col = 2;
        $info["mi_col"] = $mi_col;
        $info["maint_col"] = $maint_col;
        if ($maint_col === null) {
            $out["sheets"][] = $info;
            continue;
        }

        $count = 0;
        $samples = [];
        for ($r = $header_row + 1; $r <= $max_row; $r++) {
            $mi = cell_value($ws, $mi_col, $r);
            $maint = cell_value($ws, $maint_col, $r);
            if (!$mi || !$maint) continue;
            $mi_s = trim((string)$mi);
            if ($mi_s === "") continue;
            $t = norm_str($maint);
            if ($t === "") continue;
            $group = extract_spray_group($t);
            if ($group === "") continue;
            $count++;
            if (count($samples) < 8) {
                $samples[] = ["mi" => $mi_s, "maint" => trim((string)$maint), "group" => $group];
            }
        }
        $info["mapping_count"] = $count;
        $info["samples"] = $samples;
        $out["sheets"][] = $info;
    }
    $out["ok"] = true;
    return $out;
}

function load_work_list_rows(string $path, string $sheet_name): array {
    $rows = [];
    if (!file_exists($path)) return $rows;
    if (!file_exists(dirname(__DIR__) . "/vendor/autoload.php")) return $rows;
    require_once dirname(__DIR__) . "/vendor/autoload.php";
    $wb = \PhpOffice\PhpSpreadsheet\IOFactory::load($path);
    if (!$wb->sheetNameExists($sheet_name)) return $rows;
    $ws = $wb->getSheetByName($sheet_name);
    $po = "";
    for ($c = 1; $c <= 20; $c++) {
        $v = cell_value($ws, $c, 1);
        if ($v !== null && trim((string)$v) !== "") { $po = trim((string)$v); break; }
    }
    $max_row = $ws->getHighestRow();
    for ($r = 3; $r <= $max_row; $r++) {
        $a = cell_value($ws, 1, $r); // MP #
        $b = cell_value($ws, 2, $r); // MI # (WO)
        $d = cell_value($ws, 4, $r); // Suburb/Town
        $e = cell_value($ws, 5, $r); // Call Date
        if ($a === null && $b === null && $d === null && $e === null) continue;
        if (is_string($a) && strtolower(trim($a)) === "mp #") continue;
        $wo = $b !== null ? trim((string)$b) : "";
        if ($wo === "") continue;
        $rows[] = [
            "sheet" => $sheet_name,
            "mp" => $a !== null ? trim((string)$a) : "",
            "wo" => $wo,
            "location" => $d !== null ? trim((string)$d) : "",
            "call_date" => $e !== null ? trim((string)$e) : "",
            "po" => $po,
        ];
    }
    return $rows;
}

function log_change(string $action, string $job_key, array $fields = []): void {
    $cfg = $GLOBALS["CFG"] ?? [];
    $log_path = $cfg["change_log_path"] ?? "/var/log/ils_app_changes.log";
    $entry = [
        "ts" => date("Y-m-d H:i:s"),
        "action" => $action,
        "job_key" => $job_key,
        "ip" => $_SERVER["REMOTE_ADDR"] ?? "",
        "ua" => $_SERVER["HTTP_USER_AGENT"] ?? "",
    ];
    foreach ($fields as $k => $v) {
        $entry[$k] = $v;
    }
    $line = json_encode($entry, JSON_UNESCAPED_SLASHES);
    if ($line === false) return;
    @file_put_contents($log_path, $line . "\n", FILE_APPEND | LOCK_EX);
}

function fetch_job_fields(PDO $pdo, string $job_key): array {
    $stmt = $pdo->prepare("SELECT completed, completed_at, invoiced, invoiced_at, qty, lat, lon FROM jobs WHERE job_key = :job_key");
    $stmt->execute([":job_key" => $job_key]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    return is_array($row) ? $row : [];
}

function latest_upload(string $prefix): string {
    $dir = ensure_upload_dir();
    $files = glob($dir . "/" . $prefix . "*.xlsx");
    if (!$files) return "";
    usort($files, function ($a, $b) {
        $ta = @filemtime($a) ?: 0;
        $tb = @filemtime($b) ?: 0;
        if ($ta === $tb) return strcmp($b, $a);
        return $tb <=> $ta;
    });
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
    $base_name = $drain;
    $seg_start = null;
    $seg_end = null;
    if (preg_match('/^(.*)\\(([0-9.]+)\\s*-\\s*([0-9.]+)\\)\\s*$/', $drain, $m)) {
        $base_name = trim($m[1]);
        $seg_start = (float)$m[2];
        $seg_end = (float)$m[3];
    }
    $last_name = "";
    for ($r = $start_row; $r <= $end_row; $r++) {
        $name = trim((string)$ws->getCell("A{$r}")->getValue());
        if ($name !== "") {
            $last_name = $name;
        }
        $effective_name = $name !== "" ? $name : $last_name;
        if ($effective_name === "") {
            continue;
        }
        if (strcasecmp($effective_name, $base_name) === 0) {
            if ($seg_start !== null && $seg_end !== null) {
                $b = trim((string)$ws->getCell("B{$r}")->getValue());
                $c = trim((string)$ws->getCell("C{$r}")->getValue());
                $b_val = is_numeric($b) ? (float)$b : null;
                $c_val = is_numeric($c) ? (float)$c : null;
                if ($b_val === null || $c_val === null) {
                    continue;
                }
                if (abs($b_val - $seg_start) > 0.01 || abs($c_val - $seg_end) > 0.01) {
                    continue;
                }
            }
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
    $sql .= " ORDER BY module, sheet, item, work_order, job_key LIMIT " . $limit;

    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    foreach ($rows as &$row) {
        $unit = strtolower((string)($row["unit"] ?? ""));
        $meta = [];
        $raw_meta = $row["meta"] ?? null;
        if (is_array($raw_meta)) {
            $meta = $raw_meta;
        } elseif (is_string($raw_meta) && trim($raw_meta) !== "") {
            $parsed = json_decode($raw_meta, true);
            if (is_array($parsed)) {
                $meta = $parsed;
            }
        }
        if ($unit === "km") {
            $qty_default = $row["qty_default"] ?? null;
            $meta_km = $meta["qty_km"] ?? ($meta["qty"] ?? null);
            if (($qty_default === null || (float)$qty_default <= 0) && $meta_km !== null && (float)$meta_km > 0) {
                $row["qty_default"] = $meta_km;
            }
            if (($row["qty"] === null || (float)$row["qty"] <= 0) && $row["completed"]) {
                if ($row["qty_default"] !== null && (float)$row["qty_default"] > 0) {
                    $row["qty"] = $row["qty_default"];
                } elseif ($meta_km !== null && (float)$meta_km > 0) {
                    $row["qty"] = $meta_km;
                }
            }
        }
    }
    unset($row);
    echo json_encode(["ok" => true, "jobs" => $rows]);
    exit;
}

if ($action === "list_photos" && $method === "GET") {
    $job_key = isset($_GET["job_key"]) ? trim($_GET["job_key"]) : "";
    if ($job_key === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_job_key"]);
        exit;
    }
    $stmt = $pdo->prepare("SELECT id, job_key, filename, lat, lon, created_at FROM photos WHERE job_key = :job_key ORDER BY created_at DESC");
    $stmt->execute([":job_key" => $job_key]);
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    $base = photos_base_url($cfg);
    foreach ($rows as &$row) {
        $row["url"] = $base . "/photo.php?id=" . $row["id"] . "&key=" . urlencode($cfg["api_key"] ?? "");
    }
    unset($row);
    echo json_encode(["ok" => true, "photos" => $rows]);
    exit;
}

if ($action === "upload_photo" && $method === "POST") {
    $job_key = isset($_POST["job_key"]) ? trim($_POST["job_key"]) : "";
    if ($job_key === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_job_key"]);
        exit;
    }
    if (!isset($_FILES["photo"])) {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_file"]);
        exit;
    }
    $lat = isset($_POST["lat"]) ? trim((string)$_POST["lat"]) : "";
    $lon = isset($_POST["lon"]) ? trim((string)$_POST["lon"]) : "";
    $file = $_FILES["photo"];
    if (!is_uploaded_file($file["tmp_name"])) {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "invalid_upload"]);
        exit;
    }
    $ext = pathinfo($file["name"], PATHINFO_EXTENSION);
    $ext = $ext ? strtolower($ext) : "jpg";
    $safe_ext = preg_replace('/[^a-z0-9]+/', "", $ext);
    if ($safe_ext === "") $safe_ext = "jpg";
    $dir = ensure_photos_dir($job_key);
    $stamp = date("Ymd_His");
    $rand = substr(bin2hex(random_bytes(4)), 0, 8);
    $name = $stamp . "_" . $rand . "." . $safe_ext;
    $dest = $dir . "/" . $name;
    if (!@move_uploaded_file($file["tmp_name"], $dest)) {
        http_response_code(500);
        echo json_encode(["ok" => false, "error" => "save_failed"]);
        exit;
    }
    $stmt = $pdo->prepare("INSERT INTO photos (job_key, filename, stored_path, lat, lon) VALUES (:job_key, :filename, :stored_path, :lat, :lon)");
    $stmt->execute([
        ":job_key" => $job_key,
        ":filename" => $file["name"],
        ":stored_path" => $dest,
        ":lat" => $lat === "" ? null : $lat,
        ":lon" => $lon === "" ? null : $lon,
    ]);
    $id = $pdo->lastInsertId();
    $base = photos_base_url($cfg);
    echo json_encode([
        "ok" => true,
        "id" => $id,
        "url" => $base . "/photo.php?id=" . $id . "&key=" . urlencode($cfg["api_key"] ?? ""),
    ]);
    exit;
}

if ($action === "delete_photo" && $method === "POST") {
    $body = form_or_json();
    $id = isset($body["id"]) ? intval($body["id"]) : 0;
    if ($id <= 0) {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_id"]);
        exit;
    }
    $stmt = $pdo->prepare("SELECT stored_path FROM photos WHERE id = :id");
    $stmt->execute([":id" => $id]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!$row) {
        http_response_code(404);
        echo json_encode(["ok" => false, "error" => "not_found"]);
        exit;
    }
    $path = $row["stored_path"];
    if (is_file($path)) {
        @unlink($path);
    }
    $stmt = $pdo->prepare("DELETE FROM photos WHERE id = :id");
    $stmt->execute([":id" => $id]);
    log_change("delete_photo", "photo:" . $id, ["path" => $path]);
    echo json_encode(["ok" => true]);
    exit;
}

if ($action === "reset_from_uploads" && $method === "POST") {
    $body = form_or_json();
    $confirm = isset($body["confirm"]) ? (string)$body["confirm"] : "";
    $delete_photos = isset($body["delete_photos"]) ? (int)$body["delete_photos"] : 1;
    if ($confirm !== "YES_DELETE_ALL") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_confirm"]);
        exit;
    }

    $spray_path = master_spray_list($cfg);
    $work_path = latest_upload("work_list_");
    if ($spray_path === "" || $work_path === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_uploads", "spray" => $spray_path, "work" => $work_path]);
        exit;
    }

    try {
        $pdo->exec("DELETE FROM jobs");
        if ($delete_photos) {
            $pdo->exec("DELETE FROM photos");
            $photos_dir = ensure_upload_dir() . "/job_photos";
            if (is_dir($photos_dir)) {
                $it = new RecursiveIteratorIterator(
                    new RecursiveDirectoryIterator($photos_dir, FilesystemIterator::SKIP_DOTS),
                    RecursiveIteratorIterator::CHILD_FIRST
                );
                foreach ($it as $file) {
                    if ($file->isDir()) {
                        @rmdir($file->getPathname());
                    } else {
                        @unlink($file->getPathname());
                    }
                }
            }
        }
    } catch (Exception $e) {
        http_response_code(500);
        echo json_encode(["ok" => false, "error" => "delete_failed", "detail" => $e->getMessage()]);
        exit;
    }

    $jobs_payload = [];
    $seen_job_keys = [];

    // Spray jobs from spray list (with segments)
    $spray_rows = spray_list_rows($spray_path);
    [$po, $mapping] = work_list_mapping($work_path, "");
    foreach ($spray_rows as $r) {
        $base = strip_segment_suffix($r["drain"]);
        $group = first_token_norm($base);
        $wo = ($group !== "" && isset($mapping[$group])) ? $mapping[$group] : "";
        $job_key = $r["sheet"] . "|" . ($r["catchment"] ?? "") . "|" . $r["drain"];
        if (isset($seen_job_keys[$job_key])) {
            continue;
        }
        $seen_job_keys[$job_key] = true;
        $jobs_payload[] = [
            "job_key" => $job_key,
            "module" => "drain",
            "job_type" => "Drain spraying",
            "sheet" => $r["sheet"],
            "item" => $r["drain"],
            "lat" => $r["lat"],
            "lon" => $r["lon"],
            "work_order" => $wo,
            "po" => $wo !== "" ? $po : "",
            "unit" => "km",
            "qty_default" => (float)$r["qty_km"],
            "completed" => 0,
            "completed_at" => "",
            "invoiced" => 0,
            "invoiced_at" => "",
            "qty" => null,
            "current_work" => 0,
            "meta" => json_encode([
                "sheet" => $r["sheet"],
                "catchment" => $r["catchment"] ?? "",
                "drain" => $r["drain"],
                "qty_km" => (float)$r["qty_km"],
                "start_m" => $r["start_m"],
                "end_m" => $r["end_m"],
                "work_order" => $wo,
                "po" => $wo !== "" ? $po : "",
                "lat" => $r["lat"],
                "lon" => $r["lon"],
            ], JSON_UNESCAPED_SLASHES),
        ];
    }

    // Work list modules
    $modules = [
        ["weeds", "Noxious Weeds", "hour"],
        ["tracks", "Mtn Access Tracks", "km"],
        ["fire", "Fire Zone", "each"],
    ];
    foreach ($modules as $m) {
        [$module_id, $sheet_name, $unit] = $m;
        $rows = load_work_list_rows($work_path, $sheet_name);
        foreach ($rows as $r) {
            $job_key = $module_id . ":" . $sheet_name . ":" . $r["wo"];
            if (isset($seen_job_keys[$job_key])) {
                continue;
            }
            $seen_job_keys[$job_key] = true;
            $jobs_payload[] = [
                "job_key" => $job_key,
                "module" => $module_id,
                "job_type" => $sheet_name,
                "sheet" => $sheet_name,
                "item" => $r["location"],
                "lat" => null,
                "lon" => null,
                "work_order" => $r["wo"],
                "po" => $r["po"],
                "unit" => $unit,
                "qty_default" => null,
                "completed" => 0,
                "completed_at" => "",
                "invoiced" => 0,
                "invoiced_at" => "",
                "qty" => null,
                "current_work" => 0,
                "meta" => json_encode([
                    "location" => $r["location"],
                    "call_date" => $r["call_date"],
                    "sheet" => $sheet_name,
                ], JSON_UNESCAPED_SLASHES),
            ];
        }
    }

    if (!$jobs_payload) {
        echo json_encode(["ok" => false, "error" => "no_jobs"]);
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
        current_work = VALUES(current_work)";
    $stmt = $pdo->prepare($sql);
    foreach ($jobs_payload as $j) {
        $stmt->execute([
            ":job_key" => $j["job_key"],
            ":module" => $j["module"],
            ":job_type" => $j["job_type"],
            ":sheet" => $j["sheet"],
            ":item" => $j["item"],
            ":lat" => $j["lat"],
            ":lon" => $j["lon"],
            ":work_order" => $j["work_order"],
            ":po" => $j["po"],
            ":unit" => $j["unit"],
            ":qty_default" => $j["qty_default"],
            ":completed" => $j["completed"],
            ":completed_at" => norm_date($j["completed_at"] ?? null),
            ":invoiced" => $j["invoiced"],
            ":invoiced_at" => norm_date($j["invoiced_at"] ?? null),
            ":qty" => $j["qty"],
            ":current_work" => $j["current_work"],
            ":meta" => $j["meta"],
        ]);
    }

    log_change("reset_from_uploads", "all", ["jobs" => count($jobs_payload), "spray" => basename($spray_path), "work" => basename($work_path)]);
    echo json_encode(["ok" => true, "jobs" => count($jobs_payload), "spray" => basename($spray_path), "work" => basename($work_path)]);
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

if ($action === "debug_work_mapping" && $method === "GET") {
    $path = latest_upload("work_list_");
    if ($path === "") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_work_list"]);
        exit;
    }
    $out = work_list_mapping_debug($path);
    echo json_encode($out);
    exit;
}

if ($action === "cleanup_segment_bases" && $method === "POST") {
    $body = form_or_json();
    $confirm = isset($body["confirm"]) ? (string)$body["confirm"] : "";
    if ($confirm !== "YES_CLEANUP") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_confirm"]);
        exit;
    }
    $stmt = $pdo->prepare("SELECT job_key, sheet, item, meta FROM jobs WHERE module = 'drain'");
    $stmt->execute();
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    $has_segment = [];
    $base_candidates = [];
    foreach ($rows as $r) {
        $sheet = $r["sheet"] ?? "";
        $item = $r["item"] ?? "";
        $meta = [];
        if (isset($r["meta"]) && is_string($r["meta"]) && trim($r["meta"]) !== "") {
            $parsed = json_decode($r["meta"], true);
            if (is_array($parsed)) $meta = $parsed;
        }
        $catchment = $meta["catchment"] ?? "";
        $base = strip_segment_suffix($item);
        $key = norm_str($sheet) . "|" . norm_str($catchment) . "|" . norm_str($base);
        if (preg_match('/\\([0-9.]+\\s*-\\s*[0-9.]+\\)\\s*$/', (string)$item)) {
            $has_segment[$key] = true;
        } else {
            $base_candidates[] = ["job_key" => $r["job_key"], "key" => $key];
        }
    }
    $to_delete = [];
    foreach ($base_candidates as $c) {
        if (isset($has_segment[$c["key"]])) {
            $to_delete[] = $c["job_key"];
        }
    }
    if (!$to_delete) {
        echo json_encode(["ok" => true, "deleted" => 0]);
        exit;
    }
    $in = implode(",", array_fill(0, count($to_delete), "?"));
    $del = $pdo->prepare("DELETE FROM jobs WHERE job_key IN ($in)");
    $del->execute($to_delete);
    log_change("cleanup_segment_bases", "all", ["deleted" => count($to_delete)]);
    echo json_encode(["ok" => true, "deleted" => count($to_delete)]);
    exit;
}

if ($action === "cleanup_duplicate_drains" && $method === "POST") {
    $body = form_or_json();
    $confirm = isset($body["confirm"]) ? (string)$body["confirm"] : "";
    if ($confirm !== "YES_CLEANUP") {
        http_response_code(400);
        echo json_encode(["ok" => false, "error" => "missing_confirm"]);
        exit;
    }
    $stmt = $pdo->prepare("SELECT job_key, sheet, item, meta, updated_at FROM jobs WHERE module = 'drain'");
    $stmt->execute();
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    $keepers = [];
    $duplicates = [];
    foreach ($rows as $r) {
        $sheet = $r["sheet"] ?? "";
        $item = $r["item"] ?? "";
        $meta = [];
        if (isset($r["meta"]) && is_string($r["meta"]) && trim($r["meta"]) !== "") {
            $parsed = json_decode($r["meta"], true);
            if (is_array($parsed)) $meta = $parsed;
        }
        $catchment = $meta["catchment"] ?? "";
        $key = norm_str($sheet) . "|" . norm_str($catchment) . "|" . norm_str($item);
        $existing = $keepers[$key] ?? null;
        if (!$existing) {
            $keepers[$key] = $r;
            continue;
        }
        $existing_ts = $existing["updated_at"] ?? "";
        $curr_ts = $r["updated_at"] ?? "";
        if ($curr_ts > $existing_ts) {
            $duplicates[] = $existing["job_key"];
            $keepers[$key] = $r;
        } else {
            $duplicates[] = $r["job_key"];
        }
    }
    if (!$duplicates) {
        echo json_encode(["ok" => true, "deleted" => 0]);
        exit;
    }
    $in = implode(",", array_fill(0, count($duplicates), "?"));
    $del = $pdo->prepare("DELETE FROM jobs WHERE job_key IN ($in)");
    $del->execute($duplicates);
    log_change("cleanup_duplicate_drains", "all", ["deleted" => count($duplicates)]);
    echo json_encode(["ok" => true, "deleted" => count($duplicates)]);
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
    log_change("update_pin", $job_key, ["lat" => $lat, "lon" => $lon]);

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
            $job_key = (string)$j["job_key"];
            $before = fetch_job_fields($pdo, $job_key);
            $stmt->execute([
                ":job_key" => $job_key,
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
            $after = [
                "completed" => isset($j["completed"]) ? (int)$j["completed"] : null,
                "completed_at" => norm_date($j["completed_at"] ?? null),
                "invoiced" => isset($j["invoiced"]) ? (int)$j["invoiced"] : null,
                "invoiced_at" => norm_date($j["invoiced_at"] ?? null),
                "qty" => isset($j["qty"]) ? $j["qty"] : null,
                "lat" => $j["lat"] ?? null,
                "lon" => $j["lon"] ?? null,
            ];
            $changed = [];
            foreach ($after as $k => $v) {
                if ($v === null) continue;
                $prev = $before[$k] ?? null;
                if ((string)$prev !== (string)$v) {
                    $changed[$k] = ["from" => $prev, "to" => $v];
                }
            }
            if ($changed) {
                log_change("sync_jobs", $job_key, ["changes" => $changed]);
            }
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
    if ($qty === "") {
        $qty = null;
    }
    $completed_at = isset($body["completed_at"]) ? $body["completed_at"] : date("Y-m-d");
    $module = "";
    $unit = "";
    $qty_default = null;
    $meta = [];
    $stmt = $pdo->prepare("SELECT module, unit, qty_default, meta FROM jobs WHERE job_key = :job_key");
    $stmt->execute([":job_key" => $job_key]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if ($row) {
        if (isset($row["module"])) $module = (string)$row["module"];
        if (isset($row["unit"])) $unit = (string)$row["unit"];
        if (isset($row["qty_default"])) $qty_default = $row["qty_default"];
        $raw_meta = $row["meta"] ?? null;
        if (is_array($raw_meta)) {
            $meta = $raw_meta;
        } elseif (is_string($raw_meta) && trim($raw_meta) !== "") {
            $parsed = json_decode($raw_meta, true);
            if (is_array($parsed)) $meta = $parsed;
        }
    }
    if ($completed) {
        $qty_num = ($qty === null || $qty === "") ? null : (is_numeric($qty) ? (float)$qty : null);
        if ($qty_num === null || $qty_num <= 0) {
            $meta_km = $meta["qty_km"] ?? ($meta["qty"] ?? null);
            if ($qty_default !== null && is_numeric($qty_default) && (float)$qty_default > 0) {
                $qty = $qty_default;
            } elseif ($meta_km !== null && is_numeric($meta_km) && (float)$meta_km > 0) {
                $qty = $meta_km;
            }
        }
    }

    if ($module === "drain") {
        $sql = "UPDATE jobs SET completed = :completed, completed_at = :completed_at, qty = :qty WHERE job_key = :job_key";
        $stmt = $pdo->prepare($sql);
        $stmt->execute([
            ":completed" => $completed,
            ":completed_at" => $completed ? $completed_at : null,
            ":qty" => $qty,
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
    log_change("complete", $job_key, ["completed" => $completed, "completed_at" => $completed ? $completed_at : null, "qty" => $qty]);
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
    if ($qty === "") {
        $qty = null;
    }
    $completed_at = isset($_GET["completed_at"]) ? $_GET["completed_at"] : date("Y-m-d");
    $module = "";
    $unit = "";
    $qty_default = null;
    $meta = [];
    $stmt = $pdo->prepare("SELECT module, unit, qty_default, meta FROM jobs WHERE job_key = :job_key");
    $stmt->execute([":job_key" => $job_key]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if ($row) {
        if (isset($row["module"])) $module = (string)$row["module"];
        if (isset($row["unit"])) $unit = (string)$row["unit"];
        if (isset($row["qty_default"])) $qty_default = $row["qty_default"];
        $raw_meta = $row["meta"] ?? null;
        if (is_array($raw_meta)) {
            $meta = $raw_meta;
        } elseif (is_string($raw_meta) && trim($raw_meta) !== "") {
            $parsed = json_decode($raw_meta, true);
            if (is_array($parsed)) $meta = $parsed;
        }
    }
    if ($completed) {
        $qty_num = ($qty === null || $qty === "") ? null : (is_numeric($qty) ? (float)$qty : null);
        if ($qty_num === null || $qty_num <= 0) {
            $meta_km = $meta["qty_km"] ?? ($meta["qty"] ?? null);
            if ($qty_default !== null && is_numeric($qty_default) && (float)$qty_default > 0) {
                $qty = $qty_default;
            } elseif ($meta_km !== null && is_numeric($meta_km) && (float)$meta_km > 0) {
                $qty = $meta_km;
            }
        }
    }

    if ($module === "drain") {
        $sql = "UPDATE jobs SET completed = :completed, completed_at = :completed_at, qty = :qty WHERE job_key = :job_key";
        $stmt = $pdo->prepare($sql);
        $stmt->execute([
            ":completed" => $completed,
            ":completed_at" => $completed ? $completed_at : null,
            ":qty" => $qty,
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
    log_change("complete", $job_key, ["completed" => $completed, "completed_at" => $completed ? $completed_at : null, "qty" => $qty]);
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
    log_change("set_invoiced", $job_key, ["invoiced" => $invoiced, "invoiced_at" => $invoiced ? $invoiced_at : null]);
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
    $sql = "SELECT job_key, module, job_type, sheet, item, lat, lon, work_order, po, unit, qty_default, completed, completed_at, invoiced, invoiced_at, qty, current_work, meta, updated_at
            FROM jobs WHERE updated_at >= :since ORDER BY updated_at ASC";
    $stmt = $pdo->prepare($sql);
    $stmt->execute([":since" => $since]);
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    echo json_encode(["ok" => true, "jobs" => $rows]);
    exit;
}

http_response_code(404);
echo json_encode(["ok" => false, "error" => "unknown action"]);

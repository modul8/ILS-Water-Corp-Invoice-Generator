<?php
// Serve a single photo by id with API key access
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

$id = isset($_GET["id"]) ? intval($_GET["id"]) : 0;
if ($id <= 0) {
    http_response_code(400);
    echo "missing id";
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

$stmt = $pdo->prepare("SELECT stored_path, filename FROM photos WHERE id = :id");
$stmt->execute([":id" => $id]);
$row = $stmt->fetch(PDO::FETCH_ASSOC);
if (!$row) {
    http_response_code(404);
    echo "not found";
    exit;
}

$path = $row["stored_path"];
if (!is_file($path)) {
    http_response_code(404);
    echo "file not found";
    exit;
}

$ext = strtolower(pathinfo($path, PATHINFO_EXTENSION));
$mime = "application/octet-stream";
if (in_array($ext, ["jpg", "jpeg"])) $mime = "image/jpeg";
elseif ($ext === "png") $mime = "image/png";
elseif ($ext === "gif") $mime = "image/gif";
elseif ($ext === "webp") $mime = "image/webp";

header("Content-Type: " . $mime);
header("Content-Disposition: inline; filename=\"" . basename($row["filename"]) . "\"");
readfile($path);

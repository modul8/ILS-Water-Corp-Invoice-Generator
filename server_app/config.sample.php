<?php
return [
    "db_host" => "localhost",
    "db_port" => "3306",
    "db_name" => "ils_app_db",
    "db_user" => "ils_app_user",
    "db_pass" => "PUT_YOUR_DB_PASSWORD_HERE",
    "api_key" => "CHANGE_THIS_API_KEY",
    "ui_password" => "CHANGE_THIS_UI_PASSWORD",
    # Full path to master Spray List XLSX on the server (optional).
    # If blank, the API will use the latest spray_list_*.xlsx in /app/uploads.
    "spray_list_master" => "",
];

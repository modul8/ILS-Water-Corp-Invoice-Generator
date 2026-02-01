Mobile completion app (cPanel PHP + MariaDB)

Upload contents of this folder to:
  public_html/app

Files:
- schema.sql: Create tables in andrewdillon_wcapp
- config.sample.php: Copy to config.php and fill DB password + API key
- api/index.php: JSON API used by mobile + desktop sync
- index.php: Mobile web UI

Quick setup:
1) phpMyAdmin -> andrewdillon_wcapp -> SQL tab -> paste schema.sql
2) Copy config.sample.php to config.php and edit values
3) Upload api/index.php and index.php to public_html/app

API base URL:
  https://app.integratedliningsystems.com.au/api/index.php

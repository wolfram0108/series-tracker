#!/bin/bash
# Перезапуск стенда. Приложением управляет systemd-юнит
# series-tracker.service (автозапуск при загрузке, Restart=always),
# поэтому перезапуск — через systemd, а не прямой nohup (тот плодил бы
# второй процесс в гонке за :5000). start.sh остаётся точкой запуска,
# которую вызывает ExecStart юнита.
exec sudo systemctl restart series-tracker

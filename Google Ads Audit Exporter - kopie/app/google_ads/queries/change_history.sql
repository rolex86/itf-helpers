SELECT
{select_fields}
FROM change_event
WHERE
  change_event.change_date_time >= '{change_date_from}'
  AND change_event.change_date_time <= '{change_date_to}'
ORDER BY change_event.change_date_time DESC
LIMIT 10000

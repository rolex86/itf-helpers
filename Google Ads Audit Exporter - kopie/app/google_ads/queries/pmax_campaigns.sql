SELECT
{select_fields}
FROM campaign
WHERE
  segments.date BETWEEN '{date_from}' AND '{date_to}'
  AND campaign.advertising_channel_type = PERFORMANCE_MAX
ORDER BY metrics.cost_micros DESC

package com.goldanalyze.controlplane.market;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;

import com.goldanalyze.controlplane.market.dto.KlineResponse;

import java.sql.Date;
import java.util.List;

import java.time.ZoneOffset;

import java.time.LocalDate;
@Service
public class MarketDataService {

    private final JdbcTemplate marketJdbcTemplate;

    public MarketDataService(JdbcTemplate marketJdbcTemplate) {
        this.marketJdbcTemplate = marketJdbcTemplate;
    }

    public List<Map<String, Object>> testQuery() {

        String sql = """
                SELECT *
                FROM gold.daily_bar
                LIMIT 5
                """;

        return marketJdbcTemplate.queryForList(sql);
    }

    /**
     * 按标的和日期范围查询日线 K 线，并转换为标准 KlineResponse。
     */
    public List<KlineResponse> findDailyKlines(
            String symbol,
            LocalDate start,
            LocalDate end
    ) {
        String sql = """
                SELECT
                    trade_date,
                    open,
                    high,
                    low,
                    close,
                    volume
                FROM gold.daily_bar
                WHERE symbol = ?
                AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """;


        return marketJdbcTemplate.query(
                sql,
                (rs, rowNum) -> new KlineResponse(
                        rs.getObject("trade_date", LocalDate.class).toString(),
                        rs.getBigDecimal("open"),
                        rs.getBigDecimal("high"),
                        rs.getBigDecimal("low"),
                        rs.getBigDecimal("close"),
                        rs.getBigDecimal("volume")
                ),
                symbol,
                start,
                end
        );
    }

    /**
     * 按标的和日期范围查询 4 小时 K 线。
     * 日期范围按 UTC 自然日处理，并包含 end 指定日期的全部 K 线。
     */
    public List<KlineResponse> findFourHourKlines(
            String symbol,
            LocalDate start,
            LocalDate end
    ) {
        String sql = """
                SELECT
                    bar_time,
                    open,
                    high,
                    low,
                    close,
                    volume
                FROM gold.four_hour_bar
                WHERE symbol = ?
                AND bar_time >= ?
                AND bar_time < ?
                ORDER BY bar_time
                """;

        var startTime = start.atStartOfDay().atOffset(ZoneOffset.UTC);
        var endTime = end.plusDays(1)
                .atStartOfDay()
                .atOffset(ZoneOffset.UTC);

        return marketJdbcTemplate.query(
                sql,
                (rs, rowNum) -> new KlineResponse(
                        rs.getObject(
                                "bar_time",
                                java.time.OffsetDateTime.class
                        ).toString(),
                        rs.getBigDecimal("open"),
                        rs.getBigDecimal("high"),
                        rs.getBigDecimal("low"),
                        rs.getBigDecimal("close"),
                        rs.getBigDecimal("volume")
                ),
                symbol,
                startTime,
                endTime
        );
    }

    /**
     * 按标的和日期范围查询 1 小时 K 线。
     * 日期范围按 UTC 自然日处理，并包含 end 指定日期的全部 K 线。
     */
    public List<KlineResponse> findOneHourKlines(
            String symbol,
            LocalDate start,
            LocalDate end
    ) {
        String sql = """
                SELECT
                    bar_time,
                    open,
                    high,
                    low,
                    close,
                    volume
                FROM gold.hourly_bar
                WHERE symbol = ?
                AND bar_time >= ?
                AND bar_time < ?
                ORDER BY bar_time
                """;

        var startTime = start.atStartOfDay()
                .atOffset(ZoneOffset.UTC);

        var endTime = end.plusDays(1)
                .atStartOfDay()
                .atOffset(ZoneOffset.UTC);

        return marketJdbcTemplate.query(
                sql,
                (rs, rowNum) -> new KlineResponse(
                        rs.getObject(
                                "bar_time",
                                java.time.OffsetDateTime.class
                        ).toString(),
                        rs.getBigDecimal("open"),
                        rs.getBigDecimal("high"),
                        rs.getBigDecimal("low"),
                        rs.getBigDecimal("close"),
                        rs.getBigDecimal("volume")
                ),
                symbol,
                startTime,
                endTime
        );
    }
}
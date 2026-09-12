package com.goldanalyze.controlplane.market;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;

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
}
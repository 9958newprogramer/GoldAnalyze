package com.goldanalyze.controlplane.config;

import javax.sql.DataSource;

import org.springframework.boot.jdbc.DataSourceBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jdbc.core.JdbcTemplate;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Configuration
public class MarketDataConfig {


    @Bean
    @ConfigurationProperties(prefix = "market.datasource")
    public DataSource marketDataSource() {
        return DataSourceBuilder.create().build();
    }


    @Bean
    public JdbcTemplate marketJdbcTemplate(
            @Qualifier("marketDataSource") DataSource dataSource) {

        return new JdbcTemplate(dataSource);
    }
}
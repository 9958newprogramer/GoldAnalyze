package com.goldanalyze.controlplane.config;

import javax.sql.DataSource;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.jdbc.DataSourceBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.jdbc.core.JdbcTemplate;


/**
 * 配置 GoldAnalyze Control Plane 的主业务数据库。
 *
 * <p>主数据库使用 MySQL，负责 agent_job 等任务控制面数据；
 * PostgreSQL marketDataSource 继续专门负责行情数据。</p>
 */
@Configuration
public class AppDataSourceConfig {

    /**
     * 创建 Control Plane 主 MySQL 数据源。
     *
     * @param url MySQL JDBC 地址
     * @param username MySQL 用户名
     * @param password MySQL 密码
     * @param driverClassName MySQL JDBC 驱动类
     * @return 主业务数据源
     */
    @Bean(name = "appDataSource")
    @Primary
    public DataSource appDataSource(
            @Value("${spring.datasource.url}") String url,
            @Value("${spring.datasource.username}") String username,
            @Value("${spring.datasource.password}") String password,
            @Value("${spring.datasource.driver-class-name}") String driverClassName
    ) {
        return DataSourceBuilder.create()
                .url(url)
                .username(username)
                .password(password)
                .driverClassName(driverClassName)
                .build();
    }

    /**
     * 创建访问 MySQL 控制面数据库的 JdbcTemplate。
     *
     * @param dataSource 主 MySQL 数据源
     * @return 主数据库 JdbcTemplate
     */
    @Bean(name = "appJdbcTemplate")
    @Primary
    public JdbcTemplate appJdbcTemplate(
            @Qualifier("appDataSource") DataSource dataSource
    ) {
        return new JdbcTemplate(dataSource);
    }
}
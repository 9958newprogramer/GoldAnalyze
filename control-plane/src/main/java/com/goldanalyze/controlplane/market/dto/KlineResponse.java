package com.goldanalyze.controlplane.market.dto;

import java.math.BigDecimal;
// import java.time.LocalDate;

/**
 * 对外返回的标准 K 线数据。
 * time 表示该根 K 线对应的日期或具体时间。
 */
public record KlineResponse(
        String time,
        BigDecimal open,
        BigDecimal high,
        BigDecimal low,
        BigDecimal close,
        BigDecimal volume
) {
}
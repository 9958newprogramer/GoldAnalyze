package com.goldanalyze.controlplane.job;

import jakarta.validation.constraints.NotBlank;

public record CreateJobRequest(
        @NotBlank String question,
        @NotBlank String idempotencyKey
) {
}

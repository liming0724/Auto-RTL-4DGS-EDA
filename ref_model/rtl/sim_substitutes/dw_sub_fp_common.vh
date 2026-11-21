// ============================================================================
// SIMULATION-ONLY SUBSTITUTE -- NOT A SYNOPSYS DESIGNWARE ORIGINAL.
// Clean-room behavioral model written from the public IEEE-754 binary32
// definition so GS_RASTERIZER_pipeline.v can be simulated without licensed IP.
// Only the parameter values / ports used by rtl_baseline/gs_rasterizer are
// supported (inst_ieee_compliance = 0: subnormals flushed to zero, rnd = 0:
// round to nearest even). `status` is driven to 0. Not synthesizable (uses
// `real`); never add this directory to a synthesis file list.
// Bit-exactness against the real DesignWare models is NOT verified.
// ============================================================================
// Shared helper functions, `included inside each substitute module.
// f2r: binary32 bits -> real, subnormal inputs flushed to signed zero.
function automatic real dwsub_f2r(input [31:0] f);
    reg [63:0] d;
    begin
        if (f[30:23] == 8'h00)      d = {f[31], 63'b0};
        else if (f[30:23] == 8'hFF) d = {f[31], 11'h7FF, f[22:0], 29'b0};
        else                        d = {f[31], {3'b000, f[30:23]} + 11'd896, f[22:0], 29'b0};
        dwsub_f2r = $bitstoreal(d);
    end
endfunction

// r2f: real -> binary32 bits, round to nearest even, results below the
// normal range flushed to signed zero, overflow to signed infinity.
// Rounding binary64 results of +,-,* on binary32 operands to binary32 is
// exact (53 >= 2*24 + 2), so add/sub/mult are correctly rounded.
function automatic [31:0] dwsub_r2f(input real r);
    reg [63:0] d;
    reg [24:0] m;
    integer ue;
    begin
        d = $realtobits(r);
        if (d[62:52] == 11'h7FF) begin
            dwsub_r2f = (d[51:0] != 52'b0) ? 32'h7FC00000 : {d[63], 8'hFF, 23'b0};
        end else if (d[62:52] == 11'h000) begin
            dwsub_r2f = {d[63], 31'b0};
        end else begin
            m = {2'b01, d[51:29]};
            if (d[28] && ((|d[27:0]) || m[0])) m = m + 25'd1;
            ue = $signed({21'b0, d[62:52]}) - 1023;
            if (m[24]) begin
                ue = ue + 1;
                m = m >> 1;
            end
            if (ue > 127)       dwsub_r2f = {d[63], 8'hFF, 23'b0};
            else if (ue < -126) dwsub_r2f = {d[63], 31'b0};
            else                dwsub_r2f = {d[63], ue[7:0] + 8'd127, m[22:0]};
        end
    end
endfunction

function automatic bit dwsub_isnan(input [31:0] f);
    dwsub_isnan = (f[30:23] == 8'hFF) && (f[22:0] != 23'b0);
endfunction

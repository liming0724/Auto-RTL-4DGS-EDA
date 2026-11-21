// CACHE_WAYS-way set-associative cache of projected 2D Gaussian attributes
// {mean_x, mean_y, cov_a, cov_b, cov_c, opacity}, one SRAM_gs4d_cache per way
// holding {tag, data}; valid bits and round-robin pointers are flip-flops.
//
// Lookup (cycle L: lk_valid, lk_set, lk_tag) -> response in cycle L+1 (lk_resp):
// lk_hit / lk_rdata are combinational in that cycle (single-cycle hit lookup). The
// response also latches the write selection (sel_*): the way already holding the tag,
// else the first invalid way, else the set's round-robin victim.
//
// Write-back (wr_en, wr_data) is non-blocking: one cycle, no handshake, and always
// targets the latched selection, so every write lands in the way chosen by the most
// recent lookup. Writes in cycle L are forwarded into the response (fwd_*) and writes
// in the response cycle itself are folded in (eff2), so a lookup always sees the
// post-write set and a tag is never stored in two ways of a set.
module motion_aware_cache #(
    parameter int CACHE_SETS = 512,
    parameter int CACHE_WAYS = 2,
    parameter int DATA_WIDTH = 96,
    parameter int TAG_WIDTH  = 4
) (
    input  logic                          clk,
    input  logic                          rst_n,
    input  logic                          lk_valid,
    input  logic [$clog2(CACHE_SETS)-1:0] lk_set,
    input  logic [TAG_WIDTH-1:0]          lk_tag,
    output logic                          lk_resp,
    output logic                          lk_hit,
    output logic [DATA_WIDTH-1:0]         lk_rdata,
    input  logic                          wr_en,
    input  logic [DATA_WIDTH-1:0]         wr_data
);
    localparam int SET_W = $clog2(CACHE_SETS);
    localparam int WAY_W = CACHE_WAYS > 1 ? $clog2(CACHE_WAYS) : 1;
    localparam int WORD  = TAG_WIDTH + DATA_WIDTH;

    logic [CACHE_SETS*CACHE_WAYS-1:0] valid_q;
    logic [CACHE_SETS*WAY_W-1:0]      rr_q;

    logic                 resp_q;
    logic [SET_W-1:0]     resp_set;
    logic [TAG_WIDTH-1:0] resp_tag;
    logic                 fwd_valid;
    logic [WAY_W-1:0]     fwd_way;
    logic [WORD-1:0]      fwd_word;

    logic                 sel_valid;
    logic                 sel_hit;
    logic [SET_W-1:0]     sel_set;
    logic [WAY_W-1:0]     sel_way;
    logic [TAG_WIDTH-1:0] sel_tag;

    logic                 wr_fire;
    logic [WORD-1:0]      wr_word;
    logic [WORD-1:0]      q_word [CACHE_WAYS];
    logic [WORD-1:0]      eff2   [CACHE_WAYS];
    logic [CACHE_WAYS-1:0] v2, match, cur_valid;
    logic [WAY_W-1:0]     hit_way, victim, rr_cur, rr_eff;
    logic                 resp_same_set;

    function automatic logic [WAY_W-1:0] rr_inc(input logic [WAY_W-1:0] r);
        if (CACHE_WAYS == 1) return '0;
        if ((CACHE_WAYS & (CACHE_WAYS - 1)) != 0 && r == WAY_W'(CACHE_WAYS - 1)) return '0;
        return r + 1'b1;
    endfunction

    assign wr_fire = wr_en & sel_valid;
    assign wr_word = {sel_tag, wr_data};

    for (genvar w = 0; w < CACHE_WAYS; w++) begin : g_way
        SRAM_gs4d_cache #(.AW(SET_W), .DW(WORD)) u_sram (
            .clk(clk),
            .we(wr_fire && sel_way == WAY_W'(w)),
            .waddr(sel_set),
            .wdata(wr_word),
            .re(lk_valid),
            .raddr(lk_set),
            .rdata(q_word[w])
        );
    end

    // Response: effective post-write view of set resp_set.
    assign resp_same_set = wr_fire && sel_set == resp_set;
    assign cur_valid = valid_q[resp_set*CACHE_WAYS +: CACHE_WAYS];
    assign rr_cur = rr_q[resp_set*WAY_W +: WAY_W];
    assign rr_eff = (resp_same_set && !sel_hit) ? rr_inc(rr_cur) : rr_cur;

    always_comb begin
        for (int w = 0; w < CACHE_WAYS; w++) begin
            eff2[w] = (fwd_valid && fwd_way == WAY_W'(w)) ? fwd_word : q_word[w];
            v2[w] = cur_valid[w];
            if (resp_same_set && sel_way == WAY_W'(w)) begin
                eff2[w] = wr_word;
                v2[w] = 1'b1;
            end
            match[w] = v2[w] && eff2[w][WORD-1:DATA_WIDTH] == resp_tag;
        end
        hit_way = '0;
        victim = rr_eff;
        for (int w = CACHE_WAYS - 1; w >= 0; w--) begin
            if (match[w]) hit_way = WAY_W'(w);
            if (!v2[w]) victim = WAY_W'(w);
        end
    end

    assign lk_resp = resp_q;
    assign lk_hit = resp_q && (|match);
    assign lk_rdata = eff2[hit_way][DATA_WIDTH-1:0];

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_q   <= '0;
            rr_q      <= '0;
            resp_q    <= 1'b0;
            resp_set  <= '0;
            resp_tag  <= '0;
            fwd_valid <= 1'b0;
            fwd_way   <= '0;
            fwd_word  <= '0;
            sel_valid <= 1'b0;
            sel_hit   <= 1'b0;
            sel_set   <= '0;
            sel_way   <= '0;
            sel_tag   <= '0;
        end else begin
            resp_q <= lk_valid;
            if (lk_valid) begin
                resp_set  <= lk_set;
                resp_tag  <= lk_tag;
                fwd_valid <= wr_fire && sel_set == lk_set;
                fwd_way   <= sel_way;
                fwd_word  <= wr_word;
            end
            if (wr_fire) begin
                valid_q[sel_set*CACHE_WAYS + 32'(sel_way)] <= 1'b1;
                if (!sel_hit) rr_q[sel_set*WAY_W +: WAY_W] <= rr_inc(rr_q[sel_set*WAY_W +: WAY_W]);
                sel_hit <= 1'b1;
            end
            if (resp_q) begin
                sel_valid <= 1'b1;
                sel_set   <= resp_set;
                sel_tag   <= resp_tag;
                sel_hit   <= |match;
                sel_way   <= (|match) ? hit_way : victim;
            end
        end
    end
endmodule

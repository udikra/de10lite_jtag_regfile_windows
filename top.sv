module top(
    input  wire       MAX10_CLK1_50,
    input  wire [0:0] KEY,
    output wire [9:0] LEDR
);
    // Replace with the PLL output once the main design is added.
    wire sys_clk = MAX10_CLK1_50;

    localparam int BUF_ADDR_BITS = 12;      // buffer: 4096 words = 16 KiB

    // KEY[0] (active low) resets the sys_clk domain; release is synchronized.
    reg [1:0] rst_sync = 2'b00;
    always @(posedge sys_clk or negedge KEY[0]) begin
        if (!KEY[0]) rst_sync <= 2'b00;
        else         rst_sync <= {rst_sync[0], 1'b1};
    end
    wire sys_rst_n = rst_sync[1];

    wire [7:0][31:0] jwsr;
    wire [7:0]       jwsr_wr;
    wire [7:0][31:0] swjr;

    wire        buf_en;
    wire        buf_rd;
    wire [BUF_ADDR_BITS-1:0] buf_addr;
    wire [31:0] buf_wdata;
    wire [31:0] buf_rdata;

    // System access bus ("sa" = sys access)
    wire [31:0] sa_addr;
    wire        sa_enable;
    wire        sa_wr;
    wire [31:0] sa_data_out;
    wire [31:0] sa_data_in;
    wire        sa_ready;

    jtag_bridge #(.BUF_ADDR_BITS(BUF_ADDR_BITS)) bridge (
        .sys_clk(sys_clk),
        .sys_rst_n(sys_rst_n),
        .jwsr(jwsr),
        .jwsr_wr(jwsr_wr),
        .swjr(swjr),
        .buf_en(buf_en),
        .buf_rd(buf_rd),
        .buf_addr(buf_addr),
        .buf_wdata(buf_wdata),
        .buf_rdata(buf_rdata)
    );

    // Owns JWSR 5..7, SWJR 14..15 and the buffer's system port.
    sys_access #(.BUF_ADDR_BITS(BUF_ADDR_BITS)) access (
        .clk(sys_clk),
        .rst_n(sys_rst_n),
        .reg_addr(jwsr[5]),
        .reg_data(jwsr[6]),
        .reg_cmd(jwsr[7]),
        .reg_cmd_wr(jwsr_wr[7]),
        .reg_rdata(swjr[6]),
        .reg_status(swjr[7]),
        .buf_en(buf_en),
        .buf_rd(buf_rd),
        .buf_addr(buf_addr),
        .buf_wdata(buf_wdata),
        .buf_rdata(buf_rdata),
        .sa_addr(sa_addr),
        .sa_enable(sa_enable),
        .sa_wr(sa_wr),
        .sa_data_out(sa_data_out),
        .sa_data_in(sa_data_in),
        .sa_ready(sa_ready)
    );

    system_stub main_design (
        .clk(sys_clk),
        .rst_n(sys_rst_n),
        .jwsr(jwsr[5:0]),
        .swjr(swjr[5:0]),
        .sa_addr(sa_addr),
        .sa_enable(sa_enable),
        .sa_wr(sa_wr),
        .sa_data_out(sa_data_out),
        .sa_data_in(sa_data_in),
        .sa_ready(sa_ready)
    );

    assign LEDR = jwsr[0][9:0];
endmodule

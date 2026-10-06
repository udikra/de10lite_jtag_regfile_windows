module top(
    input  wire       MAX10_CLK1_50,
    input  wire [0:0] KEY,
    output wire [9:0] LEDR
);
    // Replace with the PLL output once the main design is added.
    wire sys_clk = MAX10_CLK1_50;

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
    wire [7:0]  buf_addr;
    wire [31:0] buf_wdata;
    wire [31:0] buf_rdata;

    jtag_bridge bridge (
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

    system_stub main_design (
        .clk(sys_clk),
        .rst_n(sys_rst_n),
        .jwsr(jwsr),
        .jwsr_wr(jwsr_wr),
        .swjr(swjr),
        .buf_en(buf_en),
        .buf_rd(buf_rd),
        .buf_addr(buf_addr),
        .buf_wdata(buf_wdata),
        .buf_rdata(buf_rdata)
    );

    assign LEDR = jwsr[0][9:0];
endmodule

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
    wire [7:0][31:0] swjr;

    jtag_bridge bridge (
        .sys_clk(sys_clk),
        .sys_rst_n(sys_rst_n),
        .jwsr(jwsr),
        .jwsr_wr(),
        .swjr(swjr)
    );

    system_stub main_design (
        .clk(sys_clk),
        .rst_n(sys_rst_n),
        .jwsr(jwsr),
        .swjr(swjr)
    );

    assign LEDR = jwsr[0][9:0];
endmodule

// reg_file.sv  -  16 x 32-bit synchronous register file
module reg_file (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,
    input  wire [3:0]  addr,
    input  wire        wr,
    input  wire [31:0] data_in,
    output reg  [31:0] data_out
);
    reg [31:0] regs [0:15];
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_out <= 32'h0;
            for (i = 0; i < 16; i = i + 1)
                regs[i] <= 32'h0;
        end else if (enable) begin
            if (wr) regs[addr] <= data_in;
            else    data_out   <= regs[addr];
        end
    end
endmodule

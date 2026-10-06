// system_stub.sv  -  placeholder for the main design (sys_clk domain)
//
// Drives each SWJR register with the bitwise inverse of the matching JWSR
// register, so both bridge directions can be checked from the host.
//
// Each JTAG write to JWSR register 7 also inverts all 256 buffer words in
// place through the buffer's system port (read, then write back, 2 cycles per
// word, about 10 us at 50 MHz), so the system side of the buffer can be
// checked too. The host must not scan the buffer during that pass.
module system_stub (
    input  wire              clk,
    input  wire              rst_n,
    input  wire  [7:0][31:0] jwsr,
    input  wire  [7:0]       jwsr_wr,
    output logic [7:0][31:0] swjr,

    output wire              buf_en,
    output wire              buf_rd,
    output wire  [7:0]       buf_addr,
    output wire  [31:0]      buf_wdata,
    input  wire  [31:0]      buf_rdata
);
    always @(posedge clk) begin
        if (!rst_n) swjr <= '0;
        else        swjr <= ~jwsr;
    end

    reg       invert_busy;
    reg       invert_write;             // 0: read word, 1: write it back inverted
    reg [7:0] invert_addr;

    always @(posedge clk) begin
        if (!rst_n) begin
            invert_busy <= 1'b0;
            invert_write <= 1'b0;
            invert_addr <= 8'd0;
        end else if (!invert_busy) begin
            invert_busy <= jwsr_wr[7];
            invert_write <= 1'b0;
            invert_addr <= 8'd0;
        end else begin
            invert_write <= !invert_write;
            if (invert_write) begin
                invert_addr <= invert_addr + 8'd1;
                invert_busy <= invert_addr != 8'd255;
            end
        end
    end

    assign buf_en = invert_busy;
    assign buf_rd = !invert_write;
    assign buf_addr = invert_addr;
    assign buf_wdata = ~buf_rdata;
endmodule

// system_stub.sv  -  placeholder for the main design (sys_clk domain)
//
// Drives each SWJR register with the bitwise inverse of the matching JWSR
// register, so both bridge directions can be checked from the host.
module system_stub (
    input  wire              clk,
    input  wire              rst_n,
    input  wire  [7:0][31:0] jwsr,
    output logic [7:0][31:0] swjr
);
    always @(posedge clk) begin
        if (!rst_n) swjr <= '0;
        else        swjr <= ~jwsr;
    end
endmodule

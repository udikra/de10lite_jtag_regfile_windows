// system_stub.sv  -  placeholder for the main design (sys_clk domain)
//
// Registers: drives SWJR register 8 + n with the bitwise inverse of JWSR
// register n (n = 0..5), so both bridge directions can be checked from the
// host. JWSR 5..7 and SWJR 14..15 belong to sys_access.sv.
//
// System address space: a 16384 x 32-bit (64 KiB) RAM answering the sa_* bus,
// aliased every 64 KiB (word address sa_addr[15:2]). To exercise the
// handshake, an LFSR randomly delays sa_ready by zero or more cycles: a
// transaction takes 2 cycles at best and about 3 on average.
module system_stub (
    input  wire              clk,
    input  wire              rst_n,
    input  wire  [5:0][31:0] jwsr,
    output logic [5:0][31:0] swjr,

    input  wire  [31:0]      sa_addr,
    input  wire              sa_enable,
    input  wire              sa_wr,
    input  wire  [31:0]      sa_data_out,
    output logic [31:0]      sa_data_in,
    output logic             sa_ready
);
    always @(posedge clk) begin
        if (!rst_n) swjr <= '0;
        else        swjr <= ~jwsr;
    end

    reg [15:0] lfsr = 16'hACE1;
    always @(posedge clk)
        lfsr <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};

    // The RAM is not reset, so it survives KEY[0].
    reg  [31:0] mem [0:16383];
    wire [13:0] word = sa_addr[15:2];
    wire        accept = sa_enable && !sa_ready && lfsr[0];

    always @(posedge clk) begin
        if (accept) begin
            if (sa_wr)
                mem[word] <= sa_data_out;
            sa_data_in <= mem[word];
        end
    end

    always @(posedge clk) begin
        if (!rst_n) sa_ready <= 1'b0;
        else        sa_ready <= accept;
    end
endmodule

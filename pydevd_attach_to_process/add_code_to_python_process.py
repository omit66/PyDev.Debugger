r'''
Copyright: Brainwy Software Ltda.

License: EPL.
=============

Works for Windows relying on a fork of winappdbg which works in py2/3 (at least for the part we're interested in).

See: https://github.com/fabioz/winappdbg (py3 branch).
Note that the official branch for winappdbg is: https://github.com/MarioVilas/winappdbg, which should be used when it works in Py3.
A private copy is added here to make deployment easier, but changes should always be done upstream first.

Works for Linux relying on gdb.

Limitations:
============

    Linux:
    ------
    
        1. It possible that ptrace is disabled: /etc/sysctl.d/10-ptrace.conf
    
        Note that even enabling it in /etc/sysctl.d/10-ptrace.conf (i.e.: making the
        ptrace_scope=0), it's possible that we need to run the application that'll use ptrace (or
        gdb in this case) as root (so, we must sudo the python which'll run this module).
        
        2. It has a simpler approach than the windows one, which may fail if the user hasn't enabled
        threading in its code. So, if the target will want to be attached to and it doesn't use threads,
        the user is advised to add:
        
        from threading import Thread
        Thread(target=str).start()
        
        to the start of its code so that the program can be attached from the outside.



Other implementations:
- pyrasite.com: 
    GPL
    Windows/linux (in Linux it's approach is actually very similar to ours, in windows the approach here is more complete).
    
- https://github.com/google/pyringe: 
    Apache v2. 
    Only linux/Python 2.

- http://pytools.codeplex.com:
    Apache V2
    Windows Only (but supports mixed mode debugging)
    Our own code relies heavily on a part of it: http://pytools.codeplex.com/SourceControl/latest#Python/Product/PyDebugAttach/PyDebugAttach.cpp
    to overcome some limitations of attaching and running code in the target python executable on Python 3.
    See: attach.cpp
     
Linux: References if we wanted to use a pure-python debugger:
    https://bitbucket.org/haypo/python-ptrace/
    http://stackoverflow.com/questions/7841573/how-to-get-an-error-message-for-errno-value-in-python
    Jugaad:
        https://www.defcon.org/images/defcon-19/dc-19-presentations/Jakhar/DEFCON-19-Jakhar-Jugaad-Linux-Thread-Injection.pdf
        https://github.com/aseemjakhar/jugaad

Something else (general and not Python related):
- http://www.codeproject.com/Articles/4610/Three-Ways-to-Inject-Your-Code-into-Another-Proces

Other references:
- https://github.com/haypo/faulthandler
- http://nedbatchelder.com/text/trace-function.html
- https://github.com/python-git/python/blob/master/Python/sysmodule.c (sys_settrace)
- https://github.com/python-git/python/blob/master/Python/ceval.c (PyEval_SetTrace)
- https://github.com/python-git/python/blob/master/Python/thread.c (PyThread_get_key_value)


To build the dlls needed on windows, visual studio express 13 was used (see compile_dll.bat)

See: attach_pydevd.py to attach the pydev debugger to a running python process.
'''

# Note: to work with nasm compiling asm to code and decompiling to see asm with shellcode:
# x:\nasm\nasm-2.07-win32\nasm-2.07\nasm.exe
# nasm.asm&x:\nasm\nasm-2.07-win32\nasm-2.07\ndisasm.exe -b arch nasm
import ctypes
import os
import struct
import subprocess
import sys
import time

class AutoExit(object):

    def __init__(self, on_exit):
        self.on_exit = on_exit

    def __enter__(self):
        pass

    def __exit__(self, *args):
        self.on_exit()


class GenShellCodeHelper(object):

    def __init__(self, is_64):
        from winappdbg import compat
        self.is_64 = is_64
        self._code = []
        if not is_64:
            self._translations = {
                'push esi': compat.b('\x56'),
                'push eax': compat.b('\x50'),
                'push ebp': compat.b('\x55'),
                'push ebx': compat.b('\x53'),

                'pop esi': compat.b('\x5E'),
                'pop eax': compat.b('\x58'),
                'pop ebp': compat.b('\x5D'),
                'pop ebx': compat.b('\x5B'),

                'mov esi': compat.b('\xBE'),
                'mov eax': compat.b('\xB8'),
                'mov ebp': compat.b('\xBD'),
                'mov ebx': compat.b('\xBB'),

                'call ebp': compat.b('\xFF\xD5'),
                'call eax': compat.b('\xFF\xD0'),
                'call ebx': compat.b('\xFF\xD3'),

                'mov ebx,eax': compat.b('\x89\xC3'),
                'mov eax,ebx': compat.b('\x89\xD8'),
                'mov ebp,esp': compat.b('\x89\xE5'),
                'mov esp,ebp': compat.b('\x89\xEC'),
                'push dword': compat.b('\x68'),

                'mov ebp,eax': compat.b('\x89\xC5'),
                'mov eax,ebp': compat.b('\x89\xE8'),

                'ret': compat.b('\xc3'),
            }
        else:
            # Translate 64 bits
            self._translations = {
                'push rsi': compat.b('\x56'),
                'push rax': compat.b('\x50'),
                'push rbp': compat.b('\x55'),
                'push rbx': compat.b('\x53'),
                'push rsp': compat.b('\x54'),
                'push rdi': compat.b('\x57'),

                'pop rsi': compat.b('\x5E'),
                'pop rax': compat.b('\x58'),
                'pop rbp': compat.b('\x5D'),
                'pop rbx': compat.b('\x5B'),
                'pop rsp': compat.b('\x5C'),
                'pop rdi': compat.b('\x5F'),

                'mov rsi': compat.b('\x48\xBE'),
                'mov rax': compat.b('\x48\xB8'),
                'mov rbp': compat.b('\x48\xBD'),
                'mov rbx': compat.b('\x48\xBB'),
                'mov rdi': compat.b('\x48\xBF'),
                'mov rcx': compat.b('\x48\xB9'),
                'mov rdx': compat.b('\x48\xBA'),

                'call rbp': compat.b('\xFF\xD5'),
                'call rax': compat.b('\xFF\xD0'),
                'call rbx': compat.b('\xFF\xD3'),

                'mov rbx,rax': compat.b('\x48\x89\xC3'),
                'mov rax,rbx': compat.b('\x48\x89\xD8'),
                'mov rbp,rsp': compat.b('\x48\x89\xE5'),
                'mov rsp,rbp': compat.b('\x48\x89\xEC'),
                'mov rcx,rbp': compat.b('\x48\x89\xE9'),

                'mov rbp,rax': compat.b('\x48\x89\xC5'),
                'mov rax,rbp': compat.b('\x48\x89\xE8'),

                'mov rdi,rbp': compat.b('\x48\x89\xEF'),

                'ret': compat.b('\xc3'),
            }

    def push_addr(self, addr):
        self._code.append(self.translate('push dword'))
        self._code.append(addr)

    def push(self, register):
        self._code.append(self.translate('push %s' % register))
        return AutoExit(lambda: self.pop(register))

    def pop(self, register):
        self._code.append(self.translate('pop %s' % register))

    def mov_to_register_addr(self, register, addr):
        self._code.append(self.translate('mov %s' % register))
        self._code.append(addr)

    def mov_register_to_from(self, register_to, register_from):
        self._code.append(self.translate('mov %s,%s' % (register_to, register_from)))

    def call(self, register):
        self._code.append(self.translate('call %s' % register))

    def preserve_stack(self):
        self.mov_register_to_from('ebp', 'esp')
        return AutoExit(lambda: self.restore_stack())

    def restore_stack(self):
        self.mov_register_to_from('esp', 'ebp')

    def ret(self):
        self._code.append(self.translate('ret'))

    def get_code(self):
        from winappdbg import compat
        return compat.b('').join(self._code)

    def translate(self, code):
        return self._translations[code]

    def pack_address(self, address):
        if self.is_64:
            return struct.pack('<q', address)
        else:
            return struct.pack('<L', address)

    def convert(self, code):
        '''
        Note:

        If the shellcode starts with '66' controls, it needs to be changed to add [BITS 32] or
        [BITS 64] to the start.

        To use:

        convert("""
            55
            53
            50
            BDE97F071E
            FFD5
            BDD67B071E
            FFD5
            5D
            5B
            58
            C3
            """)
        '''
        code = code.replace(' ', '')
        lines = []
        for l in code.splitlines(False):
            lines.append(l)
        code = ''.join(lines)  # Remove new lines
        return code.decode('hex')

def resolve_label(process, label):
    for i in range(3):
        try:
            address = process.resolve_label(label)
            assert address
            return address
        except:
            try:
                process.scan_modules()
            except:
                pass
            if i == 2:
                raise
            time.sleep(2)

def is_python_64bit():
    return (struct.calcsize('P') == 8)

def run_python_code_windows(pid, python_code, connect_debugger_tracing=False):
    assert '\'' not in python_code, 'Having a single quote messes with our command.'
    from winappdbg import compat
    from winappdbg.process import Process
    if not isinstance(python_code, compat.bytes):
        python_code = compat.b(python_code)

    process = Process(pid)
    bits = process.get_bits()
    is_64 = bits == 64

    if is_64 != is_python_64bit():
        raise RuntimeError("The architecture of the Python used to connect doesn't match the architecture of the target.\n"
        "Target 64 bits: %s\n"
        "Current Python 64 bits: %s" % (is_64, is_python_64bit()))


    assert resolve_label(process, compat.b('PyGILState_Ensure'))


    filedir = os.path.dirname(__file__)
    if is_64:
        suffix = 'amd64'
    else:
        suffix = 'x86'
    target_dll = os.path.join(filedir, 'attach_%s.dll' % suffix)
    if not os.path.exists(target_dll):
        raise RuntimeError('Could not find dll file to inject: %s' % target_dll)
    process.inject_dll(target_dll.encode('mbcs'))

    process.scan_modules()
    attach_func = resolve_label(process, compat.b('AttachAndRunPythonCode'))
    assert attach_func

    code_address = process.malloc(len(python_code))
    assert code_address
    process.write(code_address, python_code)

    return_code_address = process.malloc(ctypes.sizeof(ctypes.c_int))
    assert return_code_address
    
    CONNECT_DEBUGGER = 2
    
    startup_info = 0
    SHOW_DEBUG_INFO = 1
    # startup_info |= SHOW_DEBUG_INFO
    
    if connect_debugger_tracing:
        startup_info |= CONNECT_DEBUGGER
        
    print startup_info
    process.write_int(return_code_address, startup_info)

    helper = GenShellCodeHelper(is_64)
    if is_64:
        # Interesting read: http://msdn.microsoft.com/en-us/library/ms235286.aspx
        # Overview of x64 Calling Conventions (for windows: Linux is different!)
        # Register Usage: http://msdn.microsoft.com/en-us/library/9z1stfyw.aspx
        # The registers RAX, RCX, RDX, R8, R9, R10, R11 are considered volatile and must be considered destroyed on function calls (unless otherwise safety-provable by analysis such as whole program optimization).
        #
        # The registers RBX, RBP, RDI, RSI, RSP, R12, R13, R14, and R15 are considered nonvolatile and must be saved and restored by a function that uses them.
        #
        # Important: RCX: first int argument

        with helper.push('rdi'):  # This one REALLY must be pushed/poped
            with helper.push('rsp'):
                with helper.push('rbp'):
                    with helper.push('rbx'):

                        with helper.push('rdi'):  # Note: pop is automatic.
                            helper.mov_to_register_addr('rcx', helper.pack_address(code_address))
                            helper.mov_to_register_addr('rdx', helper.pack_address(return_code_address))
                            helper.mov_to_register_addr('rbx', helper.pack_address(attach_func))
                            helper.call('rbx')

    else:
        with helper.push('eax'):  # Note: pop is automatic.
            with helper.push('ebp'):
                with helper.push('ebx'):

                    with helper.preserve_stack():
                        # Put our code as a parameter in the stack (on x86, we push parameters to
                        # the stack)
                        helper.push_addr(helper.pack_address(return_code_address))
                        helper.push_addr(helper.pack_address(code_address))
                        helper.mov_to_register_addr('ebx', helper.pack_address(attach_func))
                        helper.call('ebx')

    helper.ret()

    code = helper.get_code()


    # Uncomment to see the disassembled version of what we just did...
#     with open('f.asm', 'wb') as stream:
#         stream.write(code)
#
#     exe = r'x:\nasm\nasm-2.07-win32\nasm-2.07\ndisasm.exe'
#     if is_64:
#         arch = '64'
#     else:
#         arch = '32'
#
#     subprocess.call((exe + ' -b %s f.asm' % arch).split())

    thread, _thread_address = process.inject_code(code, 0)

    timeout = None  # Could receive timeout in millis.
    thread.wait(timeout)

    return_code = process.read_int(return_code_address)

    process.free(thread.pInjectedMemory)
    process.free(code_address)
    process.free(return_code_address)
    return return_code


def run_python_code_linux(pid, python_code, connect_debugger_tracing=False):
    assert '\'' not in python_code, 'Having a single quote messes with our command.'
    # Note that the space in the beginning of each line in the multi-line is important!
    cmds = """-eval-command='call PyGILState_Ensure()'
 -eval-command='call PyRun_SimpleString("%s")'
 -eval-command='call PyGILState_Release($1)'""" % python_code
    cmds = cmds.replace('\r\n', '').replace('\r', '').replace('\n', '')

    cmd = 'gdb -p ' + str(pid) + ' -batch ' + cmds

    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    return out, err


if sys.platform == 'win32':
    run_python_code = run_python_code_windows
else:
    run_python_code = run_python_code_linux

def test():
    print('Running with: %s' % (sys.executable,))
    code = '''
import os, time, sys
print(os.getpid())
#from threading import Thread
#Thread(target=str).start()
if __name__ == '__main__':
    while True:
        time.sleep(.5)
        sys.stdout.write('.\\n')
        sys.stdout.flush()
'''

    p = subprocess.Popen([sys.executable, '-u', '-c', code])
    try:
        code = 'print("It worked!")\n'

        # Real code will be something as:
        # code = '''import sys;sys.path.append(r'X:\winappdbg-code\examples'); import imported;'''
        run_python_code(p.pid, python_code=code)

        time.sleep(3)
    finally:
        p.kill()

def main(args):
    # Otherwise, assume the first parameter is the pid and anything else is code to be executed
    # in the target process.
    pid = int(args[0])
    del args[0]
    python_code = ';'.join(args)

    # Note: on Linux the python code may not have a single quote char: '
    run_python_code(pid, python_code)

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print('Expected pid and Python code to execute in target process.')
    else:
        if '--test' == args[0]:
            test()
        else:
            main(args)
            


# Integer, String, float, boolean
name  ="Soham";
print(name,name+"34");
print(type(name));
a =10;
b =20;

# print(a==b, a != b, a and b, a or b, a**b, a/b, a%b);

# for i in range(5):
#     print(i);

# count = 0;
# while count < 10:
#     print(count);
#     count += 1;


# List [ ], tuple (), set {}, dict {k:v}

ls = [23,45,22];
print(ls,ls[0]);
# ls.clear();
# print(ls)
ls.append(34);
ls.insert(0,2344);
ls.extend([344,2111,9099]);
print(ls);

ls.remove(344);
ls.pop();
print(ls);

isinList =  23 in ls;
print(isinList, ls.sort(), ls.sort(reverse=True));
updatedlist = ls.sort();
print(updatedlist);